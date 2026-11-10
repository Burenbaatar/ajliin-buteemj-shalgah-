-- =============================================================================
-- Ажлын бүтээмж — Supabase схем
-- Supabase → SQL Editor → New query → энэ файлыг бүхэлд нь хуулж → Run.
-- Дахин ажиллуулахад аюулгүй (өгөгдөл устгахгүй).
--
-- Хамгаалалт:
--   * Ажилтны програм хүснэгт рүү шууд бичиж чадахгүй. Зөвхөн wt_push() функцээр,
--     компанийн нууц түлхүүр (company_key) + төхөөрөмж бүрийн нууц үгээр бичнэ.
--   * Уншихыг зөвхөн wt_managers жагсаалтад байгаа, нэвтэрсэн хэрэглэгч хийнэ.
-- =============================================================================

-- ---------------------------------------------------------------- тохиргоо
create table if not exists public.wt_settings (
  key   text primary key,
  value text not null
);
alter table public.wt_settings enable row level security;
revoke all on public.wt_settings from anon, authenticated;

-- Компанийн нууц түлхүүрийг автоматаар үүсгэнэ (config.json → cloud.company_key)
insert into public.wt_settings(key, value)
values ('company_key', replace(gen_random_uuid()::text, '-', ''))
on conflict (key) do nothing;

-- ---------------------------------------------------------------- менежерүүд
create table if not exists public.wt_managers (
  email      text primary key,
  created_at timestamptz not null default now()
);
alter table public.wt_managers enable row level security;
revoke all on public.wt_managers from anon, authenticated;

create or replace function public.wt_is_manager()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.wt_managers
    where lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;
revoke all on function public.wt_is_manager() from public;
grant execute on function public.wt_is_manager() to authenticated;

-- ---------------------------------------------------------------- төхөөрөмж (ажилтан)
create table if not exists public.wt_devices (
  id           uuid primary key,
  employee     text not null default '',
  hostname     text not null default '',
  platform     text not null default '',
  status       text not null default 'offline',   -- working | away | offline
  current_app  text,
  camera_ok    boolean,
  status_since timestamptz,
  last_seen_at timestamptz not null default now(),
  created_at   timestamptz not null default now()
);

create table if not exists public.wt_device_secrets (
  device_id   uuid primary key references public.wt_devices(id) on delete cascade,
  secret_hash text not null
);
alter table public.wt_device_secrets enable row level security;
revoke all on public.wt_device_secrets from anon, authenticated;

-- ---------------------------------------------------------------- өдрийн дүн
create table if not exists public.wt_daily (
  device_id    uuid not null references public.wt_devices(id) on delete cascade,
  day          date not null,
  employee     text not null default '',
  work_seconds integer not null default 0,
  away_seconds integer not null default 0,
  first_seen   text,
  last_seen    text,
  apps         jsonb not null default '[]'::jsonb,   -- [["Excel", 3600], ...]
  periods      jsonb not null default '[]'::jsonb,   -- [["working","09:00:00","10:15:00"], ...]
  updated_at   timestamptz not null default now(),
  primary key (device_id, day)
);
create index if not exists wt_daily_day_idx on public.wt_daily(day);

-- ---------------------------------------------------------------- уншах эрх (менежер)
alter table public.wt_devices enable row level security;
alter table public.wt_daily   enable row level security;
revoke insert, update, delete, truncate on public.wt_devices, public.wt_daily from anon, authenticated;
revoke select on public.wt_devices, public.wt_daily from anon;
grant select on public.wt_devices, public.wt_daily to authenticated;
-- Ажлаас гарсан ажилтны төхөөрөмжийг менежер устгаж болно
grant delete on public.wt_devices to authenticated;

drop policy if exists wt_devices_read on public.wt_devices;
create policy wt_devices_read on public.wt_devices
  for select to authenticated using (public.wt_is_manager());
drop policy if exists wt_devices_delete on public.wt_devices;
create policy wt_devices_delete on public.wt_devices
  for delete to authenticated using (public.wt_is_manager());
drop policy if exists wt_daily_read on public.wt_daily;
create policy wt_daily_read on public.wt_daily
  for select to authenticated using (public.wt_is_manager());

-- ---------------------------------------------------------------- бичих функц (ажилтны програм)
create or replace function public.wt_push(
  p_company_key   text,
  p_device_id     uuid,
  p_device_secret text,
  p_employee      text,
  p_hostname      text,
  p_platform      text,
  p_day           date,
  p_work_seconds  integer,
  p_away_seconds  integer,
  p_first_seen    text,
  p_last_seen     text,
  p_apps          jsonb,
  p_periods       jsonb,
  p_live          boolean default true,
  p_status        text    default null,
  p_current_app   text    default null,
  p_camera_ok     boolean default null
)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_hash     text := encode(sha256(convert_to(coalesce(p_device_secret, ''), 'UTF8')), 'hex');
  v_existing text;
begin
  if p_company_key is null
     or p_company_key is distinct from (select value from wt_settings where key = 'company_key') then
    raise exception 'invalid company key' using errcode = '28000';
  end if;
  if length(coalesce(p_device_secret, '')) < 16 then
    raise exception 'invalid device secret' using errcode = '28000';
  end if;
  if p_live and (p_status is null or p_status not in ('working', 'away', 'offline')) then
    raise exception 'invalid status' using errcode = '22023';
  end if;
  if p_day is null or p_day > current_date + 1 or p_day < current_date - 31 then
    raise exception 'invalid day' using errcode = '22023';
  end if;
  if jsonb_typeof(p_apps) is distinct from 'array' or jsonb_typeof(p_periods) is distinct from 'array'
     or pg_column_size(p_apps) + pg_column_size(p_periods) > 100000 then
    raise exception 'invalid payload' using errcode = '22023';
  end if;

  -- Төхөөрөмжийн нууц үг: анх удаа бүртгэнэ, дараа нь таарч байх ёстой
  select secret_hash into v_existing from wt_device_secrets where device_id = p_device_id;
  if v_existing is not null and v_existing <> v_hash then
    raise exception 'device secret mismatch' using errcode = '28000';
  end if;

  insert into wt_devices as d (id, employee, hostname, platform, last_seen_at)
  values (p_device_id, left(coalesce(p_employee, ''), 100), left(coalesce(p_hostname, ''), 100),
          left(coalesce(p_platform, ''), 50), now())
  on conflict (id) do update
    set employee = excluded.employee, hostname = excluded.hostname,
        platform = excluded.platform, last_seen_at = now();

  if v_existing is null then
    insert into wt_device_secrets(device_id, secret_hash) values (p_device_id, v_hash);
  end if;

  if p_live then
    update wt_devices
       set status_since = case when status is distinct from p_status then now() else status_since end,
           status       = p_status,
           current_app  = left(p_current_app, 200),
           camera_ok    = p_camera_ok
     where id = p_device_id;
  end if;

  insert into wt_daily as w (device_id, day, employee, work_seconds, away_seconds,
                             first_seen, last_seen, apps, periods, updated_at)
  values (p_device_id, p_day, left(coalesce(p_employee, ''), 100),
          greatest(0, least(coalesce(p_work_seconds, 0), 86400)),
          greatest(0, least(coalesce(p_away_seconds, 0), 86400)),
          left(p_first_seen, 8), left(p_last_seen, 8), p_apps, p_periods, now())
  on conflict (device_id, day) do update
    set employee = excluded.employee, work_seconds = excluded.work_seconds,
        away_seconds = excluded.away_seconds, first_seen = excluded.first_seen,
        last_seen = excluded.last_seen, apps = excluded.apps, periods = excluded.periods,
        updated_at = now();

  return 'ok';
end;
$$;

revoke all on function public.wt_push(text, uuid, text, text, text, text, date, integer, integer,
                                      text, text, jsonb, jsonb, boolean, text, text, boolean) from public;
grant execute on function public.wt_push(text, uuid, text, text, text, text, date, integer, integer,
                                         text, text, jsonb, jsonb, boolean, text, text, boolean)
  to anon, authenticated;

-- ---------------------------------------------------------------- дараагийн алхам
-- 1) Менежерийн имэйлийг нэмнэ (Authentication → Users хэсэгт үүсгэсэн хэрэглэгч):
--      insert into public.wt_managers(email) values ('manager@company.mn');
-- 2) Компанийн түлхүүрийг харж, config.json → cloud.company_key-д хуулна:
--      select value as company_key from public.wt_settings where key = 'company_key';
select value as company_key from public.wt_settings where key = 'company_key';
