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

-- v3: камерын төлөв, сэжигтэй үеүд, хэлтсийн Telegram чат
alter table public.wt_departments add column if not exists telegram_chat_id text;
alter table public.wt_live  add column if not exists camera_state text;          -- ok | blocked | static | none
alter table public.wt_live  add column if not exists camera_state_since timestamptz;
alter table public.wt_daily add column if not exists flags jsonb not null default '[]'::jsonb;  -- [["static","10:00:00","10:05:00"]]

-- Нууц тохиргоо (Telegram bot token г.м.) — програмаас уншиж болохгүй
create table if not exists public.wt_secrets (
  key   text primary key,
  value text not null
);
-- Илгээсэн мэдэгдэл (давхардуулахгүй)
create table if not exists public.wt_alert_log (
  user_id uuid not null,
  kind    text not null,
  key     text not null,
  message text,
  sent_at timestamptz not null default now(),
  primary key (user_id, kind, key)
);
alter table public.wt_secrets   enable row level security;
alter table public.wt_alert_log enable row level security;
revoke all on public.wt_secrets, public.wt_alert_log from anon, authenticated;

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
drop function if exists public.wt_push(uuid, text, text, date, integer, integer, text, text, jsonb, jsonb,
                                       boolean, text, text, boolean);
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
  p_camera_ok    boolean default null,
  p_flags        jsonb   default '[]'::jsonb,
  p_camera_state text    default null
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
  p_flags := coalesce(p_flags, '[]'::jsonb);
  if jsonb_typeof(p_apps) is distinct from 'array' or jsonb_typeof(p_periods) is distinct from 'array'
     or jsonb_typeof(p_flags) is distinct from 'array'
     or pg_column_size(p_apps) + pg_column_size(p_periods) + pg_column_size(p_flags) > 120000 then
    raise exception 'invalid payload' using errcode = '22023';
  end if;
  if p_camera_state is not null and p_camera_state not in ('ok', 'blocked', 'static', 'none') then
    raise exception 'invalid camera state' using errcode = '22023';
  end if;

  insert into wt_daily as w (user_id, device_id, day, hostname, work_seconds, away_seconds,
                             first_seen, last_seen, apps, periods, flags, updated_at)
  values (v_uid, p_device_id, p_day, left(coalesce(p_hostname, ''), 100),
          greatest(0, least(coalesce(p_work_seconds, 0), 86400)),
          greatest(0, least(coalesce(p_away_seconds, 0), 86400)),
          left(p_first_seen, 8), left(p_last_seen, 8), p_apps, p_periods, p_flags, now())
  on conflict (user_id, device_id, day) do update
    set hostname = excluded.hostname, work_seconds = excluded.work_seconds,
        away_seconds = excluded.away_seconds, first_seen = excluded.first_seen,
        last_seen = excluded.last_seen, apps = excluded.apps, periods = excluded.periods,
        flags = excluded.flags, updated_at = now();

  if p_live then
    insert into wt_live as l (user_id, device_id, hostname, platform, status, current_app,
                              camera_ok, camera_state, camera_state_since, status_since, last_seen_at)
    values (v_uid, p_device_id, left(coalesce(p_hostname, ''), 100), left(coalesce(p_platform, ''), 50),
            p_status, left(p_current_app, 200), p_camera_ok, p_camera_state, now(), now(), now())
    on conflict (user_id) do update
      set status_since = case when l.status is distinct from excluded.status
                                or l.device_id is distinct from excluded.device_id
                              then now() else l.status_since end,
          camera_state_since = case when l.camera_state is distinct from excluded.camera_state
                                    then now() else l.camera_state_since end,
          device_id = excluded.device_id, hostname = excluded.hostname, platform = excluded.platform,
          status = excluded.status, current_app = excluded.current_app,
          camera_ok = excluded.camera_ok, camera_state = excluded.camera_state, last_seen_at = now();
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

-- Нууц үг мартсан хүнд админ түр нууц үг тавьж өгнө (имэйл шаардлагагүй)
create or replace function public.wt_admin_set_password(p_user uuid, p_password text)
returns void language plpgsql security definer set search_path = public, extensions as $$
begin
  perform public.wt_require_admin();
  if length(coalesce(p_password, '')) < 6 then
    raise exception 'password should be at least 6 characters' using errcode = '22023';
  end if;
  update auth.users
     set encrypted_password = extensions.crypt(p_password, extensions.gen_salt('bf')), updated_at = now()
   where id = p_user;
  if not found then
    raise exception 'user not found' using errcode = '22023';
  end if;
end;
$$;

-- Telegram bot token (хадгалах/устгах). Уншиж буцаах боломжгүй — зөвхөн тохируулсан эсэх.
create or replace function public.wt_admin_set_telegram(p_token text)
returns void language plpgsql security definer set search_path = public as $$
begin
  perform public.wt_require_admin();
  if coalesce(trim(p_token), '') = '' then
    delete from wt_secrets where key = 'telegram_bot_token';
  else
    insert into wt_secrets(key, value) values ('telegram_bot_token', trim(p_token))
    on conflict (key) do update set value = excluded.value;
  end if;
end;
$$;

create or replace function public.wt_admin_telegram_configured()
returns boolean language plpgsql stable security definer set search_path = public as $$
begin
  perform public.wt_require_admin();
  return exists (select 1 from wt_secrets where key = 'telegram_bot_token');
end;
$$;

create or replace function public.wt_admin_set_department_chat(p_id bigint, p_chat text)
returns void language plpgsql security definer set search_path = public as $$
begin
  perform public.wt_require_admin();
  update wt_departments set telegram_chat_id = nullif(trim(coalesce(p_chat, '')), '') where id = p_id;
end;
$$;

-- ---------------------------------------------------------------- Telegram мэдэгдэл (сервер дээр, 5 мин тутам)
-- pg_net өргөтгөлөөр Telegram руу илгээнэ. Менежерийн компьютер унтраастай байсан ч ажиллана.
create or replace function public.wt_send_telegram(p_chat text, p_text text)
returns void language plpgsql security definer set search_path = public as $$
declare
  v_token text := (select value from wt_secrets where key = 'telegram_bot_token');
begin
  if v_token is null or coalesce(p_chat, '') = '' then
    return;
  end if;
  perform net.http_post(
    url := 'https://api.telegram.org/bot' || v_token || '/sendMessage',
    body := jsonb_build_object('chat_id', p_chat, 'text', p_text),
    headers := '{"Content-Type": "application/json"}'::jsonb);
end;
$$;

-- Нэг мэдэгдлийг нэг л удаа: хэлтсийн чат + админы чат руу
create or replace function public.wt_alert(p_user uuid, p_kind text, p_key text, p_text text)
returns int language plpgsql security definer set search_path = public as $$
declare
  v_dept_chat text;
  v_admin_chat text := (select settings ->> 'telegram_admin_chat' from wt_company where id = 1);
begin
  insert into wt_alert_log(user_id, kind, key, message) values (p_user, p_kind, p_key, p_text)
  on conflict do nothing;
  if not found then
    return 0;
  end if;
  select d.telegram_chat_id into v_dept_chat
    from wt_profiles p join wt_departments d on d.id = p.department_id where p.user_id = p_user;
  perform public.wt_send_telegram(v_dept_chat, p_text);
  if v_admin_chat is distinct from v_dept_chat then
    perform public.wt_send_telegram(v_admin_chat, p_text);
  end if;
  return 1;
end;
$$;

create or replace function public.wt_run_alerts()
returns int language plpgsql security definer set search_path = public as $$
declare
  s        jsonb := coalesce((select settings from wt_company where id = 1), '{}'::jsonb);
  tz       text := coalesce(s ->> 'timezone', 'Asia/Ulaanbaatar');
  now_l    timestamp := now() at time zone tz;
  today    date := (now() at time zone tz)::date;
  t        time := (now() at time zone tz)::time;
  ws       time := coalesce(s ->> 'work_start', '09:00')::time;
  we       time := coalesce(s ->> 'work_end', '18:00')::time;
  ls       time := nullif(s ->> 'lunch_start', '')::time;
  le       time := nullif(s ->> 'lunch_end', '')::time;
  workdays jsonb := coalesce(s -> 'workdays', '[1,2,3,4,5]'::jsonb);   -- 1 = Даваа
  late_min int := coalesce((s ->> 'alert_late_min')::int, 30);
  away_min int := coalesce((s ->> 'alert_away_min')::int, 40);
  off_min  int := coalesce((s ->> 'alert_offline_min')::int, 15);
  in_lunch boolean;
  n        int := 0;
  r        record;
begin
  if not coalesce((s ->> 'alerts_enabled')::boolean, false)
     or not exists (select 1 from wt_secrets where key = 'telegram_bot_token') then
    return 0;
  end if;
  if not workdays @> to_jsonb(extract(isodow from today)::int) or t < ws or t > we then
    return 0;
  end if;
  in_lunch := ls is not null and le is not null and t >= ls and t < le;

  -- 1) Ирээгүй
  if t >= ws + make_interval(mins => late_min) then
    for r in
      select p.user_id, coalesce(nullif(p.full_name, ''), p.email) as name
      from wt_profiles p
      where p.active and p.role <> 'pending' and p.created_at::date < today
        and not exists (select 1 from wt_daily d where d.user_id = p.user_id and d.day = today and d.work_seconds > 0)
    loop
      n := n + public.wt_alert(r.user_id, 'not_arrived', today::text,
             format('⏰ %s %s болтол ирээгүй байна', r.name, to_char(ws + make_interval(mins => late_min), 'HH24:MI')));
    end loop;
  end if;

  -- 2) Удаан гарсан (цайны цагаас бусад үед)
  if not in_lunch then
    for r in
      select l.user_id, l.status_since, coalesce(nullif(p.full_name, ''), p.email) as name
      from wt_live l join wt_profiles p using (user_id)
      where p.active and l.status = 'away' and l.last_seen_at > now() - interval '3 minutes'
        and l.status_since < now() - make_interval(mins => away_min)
    loop
      n := n + public.wt_alert(r.user_id, 'long_away', r.status_since::text,
             format('🚶 %s %s-с хойш (%s+ минут) ажлын байрандаа алга', r.name,
                    to_char(r.status_since at time zone tz, 'HH24:MI'), away_min));
    end loop;
  end if;

  -- 3) Ажлын цагаар програм хаагдсан / офлайн
  for r in
    select l.user_id, l.last_seen_at, coalesce(nullif(p.full_name, ''), p.email) as name
    from wt_live l join wt_profiles p using (user_id)
    where p.active
      and l.last_seen_at < now() - make_interval(mins => off_min)
      and (l.last_seen_at at time zone tz)::date = today
      and (l.last_seen_at at time zone tz)::time between ws and we
      and not (in_lunch and (l.last_seen_at at time zone tz)::time >= ls)
  loop
    n := n + public.wt_alert(r.user_id, 'offline', r.last_seen_at::text,
           format('⚠️ %s-ийн програм %s-с хойш хаалттай/офлайн байна', r.name,
                  to_char(r.last_seen_at at time zone tz, 'HH24:MI')));
  end loop;

  -- 4) Камер таглагдсан / хөдөлгөөнгүй царай (зураг?)
  for r in
    select l.user_id, l.camera_state, l.camera_state_since, coalesce(nullif(p.full_name, ''), p.email) as name
    from wt_live l join wt_profiles p using (user_id)
    where p.active and l.camera_state in ('blocked', 'static') and l.last_seen_at > now() - interval '3 minutes'
  loop
    n := n + public.wt_alert(r.user_id, 'camera_' || r.camera_state, coalesce(r.camera_state_since::text, today::text),
           format(case r.camera_state when 'blocked' then '📷 %s-ийн камер таглагдсан байна (%s-с)'
                                      else '🖼 %s: камерт хөдөлгөөнгүй царай (зураг байж магадгүй) %s-с' end,
                  r.name, to_char(r.camera_state_since at time zone tz, 'HH24:MI')));
  end loop;

  delete from wt_alert_log where sent_at < now() - interval '60 days';
  return n;
end;
$$;

-- pg_cron + pg_net байвал 5 минут тутам ажиллуулна (Supabase: Database → Extensions-ээс асааж болно)
do $$
begin
  begin
    create extension if not exists pg_net;
  exception when others then
    raise notice 'pg_net асаагүй: Dashboard → Database → Extensions → pg_net (%).', sqlerrm;
  end;
  begin
    create extension if not exists pg_cron;
    perform cron.unschedule(jobid) from cron.job where jobname = 'wt-alerts';
    perform cron.schedule('wt-alerts', '*/5 * * * *', 'select public.wt_run_alerts()');
  exception when others then
    raise notice 'pg_cron асаагүй: Dashboard → Database → Extensions → pg_cron (%). Telegram мэдэгдэл ажиллахгүй.', sqlerrm;
  end;
end $$;

-- ---------------------------------------------------------------- функцийн эрх
-- Програм (нэвтэрсэн хэрэглэгч) дуудаж болох функцууд. Доторх эрхийн шалгалтыг функц өөрөө хийнэ.
revoke all on function public.wt_my_role() from public, anon;
grant execute on function public.wt_my_role() to authenticated;
revoke all on function public.wt_can_view(uuid) from public, anon;
grant execute on function public.wt_can_view(uuid) to authenticated;
revoke all on function public.wt_require_admin() from public, anon;
grant execute on function public.wt_require_admin() to authenticated;
revoke all on function public.wt_push(uuid, text, text, date, integer, integer, text, text, jsonb, jsonb, boolean, text, text, boolean, jsonb, text) from public, anon;
grant execute on function public.wt_push(uuid, text, text, date, integer, integer, text, text, jsonb, jsonb, boolean, text, text, boolean, jsonb, text) to authenticated;
revoke all on function public.wt_admin_update_user(uuid, text, text, bigint, boolean) from public, anon;
grant execute on function public.wt_admin_update_user(uuid, text, text, bigint, boolean) to authenticated;
revoke all on function public.wt_admin_save_department(bigint, text) from public, anon;
grant execute on function public.wt_admin_save_department(bigint, text) to authenticated;
revoke all on function public.wt_admin_delete_department(bigint) from public, anon;
grant execute on function public.wt_admin_delete_department(bigint) to authenticated;
revoke all on function public.wt_admin_save_settings(jsonb) from public, anon;
grant execute on function public.wt_admin_save_settings(jsonb) to authenticated;
revoke all on function public.wt_admin_delete_user_data(uuid) from public, anon;
grant execute on function public.wt_admin_delete_user_data(uuid) to authenticated;
revoke all on function public.wt_admin_set_password(uuid, text) from public, anon;
grant execute on function public.wt_admin_set_password(uuid, text) to authenticated;
revoke all on function public.wt_admin_set_telegram(text) from public, anon;
grant execute on function public.wt_admin_set_telegram(text) to authenticated;
revoke all on function public.wt_admin_telegram_configured() from public, anon;
grant execute on function public.wt_admin_telegram_configured() to authenticated;
revoke all on function public.wt_admin_set_department_chat(bigint, text) from public, anon;
grant execute on function public.wt_admin_set_department_chat(bigint, text) to authenticated;
-- Зөвхөн сервер дотор (trigger, pg_cron) ажиллах функцууд
revoke all on function public.wt_handle_new_user() from public, anon, authenticated;
revoke all on function public.wt_send_telegram(text, text) from public, anon, authenticated;
revoke all on function public.wt_alert(uuid, text, text, text) from public, anon, authenticated;
revoke all on function public.wt_run_alerts() from public, anon, authenticated;

notify pgrst, 'reload schema';
select 'OK — схем бэлэн' as result;
