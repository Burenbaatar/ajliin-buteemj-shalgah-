-- =============================================================================
-- Ажлын бүтээмж — Supabase схем (v2: нэвтрэх эрхтэй)
-- Supabase → SQL Editor → New query → энэ файлыг бүхэлд нь хуулж → Run.
-- Дахин ажиллуулахад аюулгүй (өгөгдөл устгахгүй).
--
-- Эрх:
--   pending  — бүртгүүлсэн, админ баталгаажуулаагүй (юу ч хийж чадахгүй)
--   employee — өөрийн цагийг бүртгэнэ, өөрийнхөө мэдээллийг харна
--   manager  — + өөрийн хэлтсийн ажилтнуудыг харна
--   admin    — + бүх хүнийг харна, эрх/хэлтэс/тохиргоо өөрчилнө
-- АНХ БҮРТГҮҮЛСЭН ХҮН АВТОМАТААР АДМИН болно.
-- =============================================================================

-- ---------------------------------------------------------------- v1 (нэвтрэлтгүй) хувилбарыг цэвэрлэх
do $$ begin
  if exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'wt_daily' and column_name = 'device_id')
     and not exists (select 1 from information_schema.columns
             where table_schema = 'public' and table_name = 'wt_daily' and column_name = 'user_id') then
    drop table public.wt_daily cascade;
  end if;
end $$;
drop table if exists public.wt_device_secrets, public.wt_devices, public.wt_managers, public.wt_settings cascade;
drop function if exists public.wt_is_manager();
drop function if exists public.wt_push(text, uuid, text, text, text, text, date, integer, integer,
                                       text, text, jsonb, jsonb, boolean, text, text, boolean);

-- ---------------------------------------------------------------- хүснэгтүүд
create table if not exists public.wt_departments (
  id         bigint generated always as identity primary key,
  name       text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.wt_profiles (
  user_id       uuid primary key references auth.users(id) on delete cascade,
  email         text not null default '',
  full_name     text not null default '',
  role          text not null default 'pending' check (role in ('pending', 'employee', 'manager', 'admin')),
  department_id bigint references public.wt_departments(id) on delete set null,
  active        boolean not null default true,
  created_at    timestamptz not null default now()
);

-- Компанийн нийтлэг тохиргоо (админ өөрчилнө, бүх програм уншина)
create table if not exists public.wt_company (
  id       int primary key default 1 check (id = 1),
  settings jsonb not null default '{}'::jsonb
);
insert into public.wt_company(id) values (1) on conflict (id) do nothing;

-- Хүн бүрийн одоогийн төлөв
create table if not exists public.wt_live (
  user_id      uuid primary key references public.wt_profiles(user_id) on delete cascade,
  device_id    uuid,
  hostname     text not null default '',
  platform     text not null default '',
  status       text not null default 'offline',   -- working | away | offline
  current_app  text,
  camera_ok    boolean,
  status_since timestamptz,
  last_seen_at timestamptz not null default now()
);

-- Өдрийн дүн (нэг хүн хэд хэдэн компьютер дээр ажилласан бол мөр тус бүр)
create table if not exists public.wt_daily (
  user_id      uuid not null references public.wt_profiles(user_id) on delete cascade,
  device_id    uuid not null,
  day          date not null,
  hostname     text not null default '',
  work_seconds integer not null default 0,
  away_seconds integer not null default 0,
  first_seen   text,
  last_seen    text,
  apps         jsonb not null default '[]'::jsonb,   -- [["Excel", 3600], ...]
  periods      jsonb not null default '[]'::jsonb,   -- [["working","09:00:00","10:15:00"], ...]
  updated_at   timestamptz not null default now(),
  primary key (user_id, device_id, day)
);
create index if not exists wt_daily_day_idx on public.wt_daily(day);

-- ---------------------------------------------------------------- эрхийн туслах функцууд
create or replace function public.wt_my_role()
returns text language sql stable security definer set search_path = public as $$
  select role from public.wt_profiles where user_id = auth.uid() and active;
$$;

-- Нэвтэрсэн хэрэглэгч p_user-ийн мэдээллийг харж болох уу?
create or replace function public.wt_can_view(p_user uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select case
    when auth.uid() is null then false
    when p_user = auth.uid() then true
    else exists (
      select 1
      from public.wt_profiles me
      join public.wt_profiles t on t.user_id = p_user
      where me.user_id = auth.uid() and me.active
        and (me.role = 'admin'
             or (me.role = 'manager' and me.department_id is not null and me.department_id = t.department_id))
    )
  end;
$$;

-- ---------------------------------------------------------------- шинэ хэрэглэгч → profile
create or replace function public.wt_handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into public.wt_profiles(user_id, email, full_name, role)
  values (new.id, coalesce(new.email, ''), left(coalesce(new.raw_user_meta_data ->> 'full_name', ''), 100),
          case when exists (select 1 from public.wt_profiles where role = 'admin') then 'pending' else 'admin' end)
  on conflict (user_id) do nothing;
  return new;
end;
$$;
drop trigger if exists wt_on_auth_user_created on auth.users;
create trigger wt_on_auth_user_created
  after insert on auth.users for each row execute function public.wt_handle_new_user();

-- Схемээс өмнө үүссэн хэрэглэгчид (байвал)
insert into public.wt_profiles(user_id, email, full_name)
select u.id, coalesce(u.email, ''), left(coalesce(u.raw_user_meta_data ->> 'full_name', ''), 100)
from auth.users u
on conflict (user_id) do nothing;
update public.wt_profiles set role = 'admin'
where user_id = (select id from auth.users order by created_at limit 1)
  and not exists (select 1 from public.wt_profiles where role = 'admin');

-- ---------------------------------------------------------------- унших эрх (RLS)
alter table public.wt_departments enable row level security;
alter table public.wt_profiles    enable row level security;
alter table public.wt_company     enable row level security;
alter table public.wt_live        enable row level security;
alter table public.wt_daily       enable row level security;

revoke all on public.wt_departments, public.wt_profiles, public.wt_company, public.wt_live, public.wt_daily
  from anon, authenticated;
grant select on public.wt_departments, public.wt_profiles, public.wt_company, public.wt_live, public.wt_daily
  to authenticated;

drop policy if exists wt_departments_read on public.wt_departments;
create policy wt_departments_read on public.wt_departments for select to authenticated using (true);
drop policy if exists wt_company_read on public.wt_company;
create policy wt_company_read on public.wt_company for select to authenticated using (true);
drop policy if exists wt_profiles_read on public.wt_profiles;
create policy wt_profiles_read on public.wt_profiles for select to authenticated using (public.wt_can_view(user_id));
drop policy if exists wt_live_read on public.wt_live;
create policy wt_live_read on public.wt_live for select to authenticated using (public.wt_can_view(user_id));
drop policy if exists wt_daily_read on public.wt_daily;
create policy wt_daily_read on public.wt_daily for select to authenticated using (public.wt_can_view(user_id));

-- ---------------------------------------------------------------- бичих: програм (нэвтэрсэн хэрэглэгч өөрийнхөө)
create or replace function public.wt_push(
  p_device_id    uuid,
  p_hostname     text,
  p_platform     text,
  p_day          date,
  p_work_seconds integer,
  p_away_seconds integer,
  p_first_seen   text,
  p_last_seen    text,
  p_apps         jsonb,
  p_periods      jsonb,
  p_live         boolean default true,
  p_status       text    default null,
  p_current_app  text    default null,
  p_camera_ok    boolean default null
)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_uid  uuid := auth.uid();
  v_role text := public.wt_my_role();
begin
  if v_uid is null then
    raise exception 'not authenticated' using errcode = '28000';
  end if;
  if v_role is null or v_role = 'pending' then
    raise exception 'account not approved' using errcode = '42501';
  end if;
  if p_device_id is null then
    raise exception 'invalid device' using errcode = '22023';
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

  insert into wt_daily as w (user_id, device_id, day, hostname, work_seconds, away_seconds,
                             first_seen, last_seen, apps, periods, updated_at)
  values (v_uid, p_device_id, p_day, left(coalesce(p_hostname, ''), 100),
          greatest(0, least(coalesce(p_work_seconds, 0), 86400)),
          greatest(0, least(coalesce(p_away_seconds, 0), 86400)),
          left(p_first_seen, 8), left(p_last_seen, 8), p_apps, p_periods, now())
  on conflict (user_id, device_id, day) do update
    set hostname = excluded.hostname, work_seconds = excluded.work_seconds,
        away_seconds = excluded.away_seconds, first_seen = excluded.first_seen,
        last_seen = excluded.last_seen, apps = excluded.apps, periods = excluded.periods,
        updated_at = now();

  if p_live then
    insert into wt_live as l (user_id, device_id, hostname, platform, status, current_app,
                              camera_ok, status_since, last_seen_at)
    values (v_uid, p_device_id, left(coalesce(p_hostname, ''), 100), left(coalesce(p_platform, ''), 50),
            p_status, left(p_current_app, 200), p_camera_ok, now(), now())
    on conflict (user_id) do update
      set status_since = case when l.status is distinct from excluded.status
                                or l.device_id is distinct from excluded.device_id
                              then now() else l.status_since end,
          device_id = excluded.device_id, hostname = excluded.hostname, platform = excluded.platform,
          status = excluded.status, current_app = excluded.current_app,
          camera_ok = excluded.camera_ok, last_seen_at = now();
  end if;
  return 'ok';
end;
$$;

-- ---------------------------------------------------------------- админы үйлдлүүд
create or replace function public.wt_require_admin()
returns void language plpgsql stable security definer set search_path = public as $$
begin
  if public.wt_my_role() is distinct from 'admin' then
    raise exception 'admin only' using errcode = '42501';
  end if;
end;
$$;

create or replace function public.wt_admin_update_user(
  p_user uuid, p_full_name text, p_role text, p_department_id bigint, p_active boolean
)
returns void language plpgsql security definer set search_path = public as $$
declare
  v_admins int;
begin
  perform public.wt_require_admin();
  if p_role not in ('pending', 'employee', 'manager', 'admin') then
    raise exception 'invalid role' using errcode = '22023';
  end if;
  select count(*) into v_admins from wt_profiles where role = 'admin' and active and user_id <> p_user;
  if v_admins = 0 and (p_role <> 'admin' or not p_active) then
    raise exception 'last admin cannot be removed' using errcode = '42501';
  end if;
  update wt_profiles
     set full_name = left(coalesce(nullif(trim(p_full_name), ''), full_name), 100),
         role = p_role, department_id = p_department_id, active = p_active
   where user_id = p_user;
  if not found then
    raise exception 'user not found' using errcode = '22023';
  end if;
end;
$$;

create or replace function public.wt_admin_save_department(p_id bigint, p_name text)
returns bigint language plpgsql security definer set search_path = public as $$
declare
  v_id bigint;
begin
  perform public.wt_require_admin();
  if coalesce(trim(p_name), '') = '' then
    raise exception 'name required' using errcode = '22023';
  end if;
  if p_id is null then
    insert into wt_departments(name) values (left(trim(p_name), 100)) returning id into v_id;
  else
    update wt_departments set name = left(trim(p_name), 100) where id = p_id returning id into v_id;
  end if;
  return v_id;
end;
$$;

create or replace function public.wt_admin_delete_department(p_id bigint)
returns void language plpgsql security definer set search_path = public as $$
begin
  perform public.wt_require_admin();
  delete from wt_departments where id = p_id;
end;
$$;

create or replace function public.wt_admin_save_settings(p_settings jsonb)
returns void language plpgsql security definer set search_path = public as $$
begin
  perform public.wt_require_admin();
  if jsonb_typeof(p_settings) is distinct from 'object' then
    raise exception 'invalid settings' using errcode = '22023';
  end if;
  update wt_company set settings = p_settings where id = 1;
end;
$$;

-- Ажлаас гарсан хүний цагийн түүхийг устгах (бүртгэл нь үлдэнэ, идэвхгүй болгоно)
create or replace function public.wt_admin_delete_user_data(p_user uuid)
returns void language plpgsql security definer set search_path = public as $$
begin
  perform public.wt_require_admin();
  if p_user = auth.uid() then
    raise exception 'cannot delete yourself' using errcode = '42501';
  end if;
  delete from wt_daily where user_id = p_user;
  delete from wt_live where user_id = p_user;
  update wt_profiles set active = false where user_id = p_user and role <> 'admin';
end;
$$;

-- ---------------------------------------------------------------- функцийн эрх
revoke all on function public.wt_my_role()                    from public, anon;
revoke all on function public.wt_can_view(uuid)               from public, anon;
revoke all on function public.wt_handle_new_user()            from public, anon, authenticated;
revoke all on function public.wt_require_admin()              from public, anon;
revoke all on function public.wt_push(uuid, text, text, date, integer, integer, text, text, jsonb, jsonb,
                                      boolean, text, text, boolean) from public, anon;
revoke all on function public.wt_admin_update_user(uuid, text, text, bigint, boolean) from public, anon;
revoke all on function public.wt_admin_save_department(bigint, text)  from public, anon;
revoke all on function public.wt_admin_delete_department(bigint)      from public, anon;
revoke all on function public.wt_admin_save_settings(jsonb)           from public, anon;
revoke all on function public.wt_admin_delete_user_data(uuid)         from public, anon;

grant execute on function public.wt_my_role()      to authenticated;
grant execute on function public.wt_can_view(uuid) to authenticated;
grant execute on function public.wt_require_admin() to authenticated;
grant execute on function public.wt_push(uuid, text, text, date, integer, integer, text, text, jsonb, jsonb,
                                         boolean, text, text, boolean) to authenticated;
grant execute on function public.wt_admin_update_user(uuid, text, text, bigint, boolean) to authenticated;
grant execute on function public.wt_admin_save_department(bigint, text)  to authenticated;
grant execute on function public.wt_admin_delete_department(bigint)      to authenticated;
grant execute on function public.wt_admin_save_settings(jsonb)           to authenticated;
grant execute on function public.wt_admin_delete_user_data(uuid)         to authenticated;

notify pgrst, 'reload schema';
select 'OK — схем бэлэн' as result;
