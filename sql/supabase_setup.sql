-- Моя ИИшка 4.1.5: E2E-encrypted cloud vault for Supabase.
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
