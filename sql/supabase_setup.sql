-- Моя ИИшка 4.1.9: E2E-encrypted cloud vault and private backup storage.
-- Run this entire script in Supabase Dashboard -> SQL Editor -> New query.
-- Do NOT create or paste a service_role/secret key into the desktop application.

create table if not exists public.moyaishka_vault (
    user_id uuid primary key references auth.users (id) on delete cascade,
    ciphertext text not null,
    salt text not null,
    nonce text not null,
    schema_version integer not null default 1 check (schema_version = 1),
    updated_at timestamptz not null default now()
);

alter table public.moyaishka_vault enable row level security;

revoke all on table public.moyaishka_vault from anon;
grant select, insert, update, delete on table public.moyaishka_vault to authenticated;

drop policy if exists "Users can read their own MoyaIshka vault" on public.moyaishka_vault;
drop policy if exists "Users can create their own MoyaIshka vault" on public.moyaishka_vault;
drop policy if exists "Users can update their own MoyaIshka vault" on public.moyaishka_vault;
drop policy if exists "Users can delete their own MoyaIshka vault" on public.moyaishka_vault;

create policy "Users can read their own MoyaIshka vault"
    on public.moyaishka_vault for select to authenticated
    using ((select auth.uid()) = user_id);

create policy "Users can create their own MoyaIshka vault"
    on public.moyaishka_vault for insert to authenticated
    with check ((select auth.uid()) = user_id);

create policy "Users can update their own MoyaIshka vault"
    on public.moyaishka_vault for update to authenticated
    using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

create policy "Users can delete their own MoyaIshka vault"
    on public.moyaishka_vault for delete to authenticated
    using ((select auth.uid()) = user_id);

-- Private ZIP backup mirror. Each account can access only its own <auth.uid()>/ prefix.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('moyaishka-backups', 'moyaishka-backups', false, 50000000, array['application/octet-stream']::text[])
on conflict (id) do update set public=false, file_size_limit=50000000,
    allowed_mime_types=array['application/octet-stream']::text[];

drop policy if exists "MoyaIshka backups: own objects read" on storage.objects;
drop policy if exists "MoyaIshka backups: own objects upload" on storage.objects;
drop policy if exists "MoyaIshka backups: own objects delete" on storage.objects;

create policy "MoyaIshka backups: own objects read" on storage.objects
    for select to authenticated using (
        bucket_id='moyaishka-backups' and (storage.foldername(name))[1]=(select auth.uid()::text)
    );
create policy "MoyaIshka backups: own objects upload" on storage.objects
    for insert to authenticated with check (
        bucket_id='moyaishka-backups' and (storage.foldername(name))[1]=(select auth.uid()::text)
    );
create policy "MoyaIshka backups: own objects delete" on storage.objects
    for delete to authenticated using (
        bucket_id='moyaishka-backups' and (storage.foldername(name))[1]=(select auth.uid()::text)
    );
