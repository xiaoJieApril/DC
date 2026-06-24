create table if not exists public.dc_messages (
  guild_id text not null,
  message_id text not null,
  channel_id text not null default '',
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (guild_id, message_id)
);

create table if not exists public.dc_reaction_roles (
  guild_id text not null,
  message_id text not null,
  channel_id text not null default '',
  payload jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (guild_id, message_id)
);

alter table public.dc_messages enable row level security;
alter table public.dc_reaction_roles enable row level security;

create index if not exists dc_messages_updated_at_idx
  on public.dc_messages (updated_at desc);

create index if not exists dc_reaction_roles_updated_at_idx
  on public.dc_reaction_roles (updated_at desc);
