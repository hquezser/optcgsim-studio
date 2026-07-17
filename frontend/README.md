# Frontend — structure modulaire multiplateforme

Objectif : les composants React doivent survivre au passage web → desktop (Tauri) →
mobile (Tauri Mobile ou React Native) **sans réécriture de la logique métier**. La règle
d'architecture qui rend ça possible :

> **Toute la logique vit dans `core/` (TypeScript pur, zéro import React/DOM).
> Les composants ne font que rendre. Les accès plateforme passent par `adapters/`.**

```
frontend/
├── core/                       # TS pur — portable partout (web/Tauri/RN/tests node)
│   ├── types.ts                # Decklist, Profile, CosmeticPack, SyncReport… (miroir du
│   │                           #   schéma SQL — mêmes colonnes de réplication)
│   ├── api.ts                  # client du backend studio (fetch REST localhost / cloud)
│   ├── decklist.ts             # parsing/validation côté client (mêmes règles que Python :
│   │                           #   1 leader + 50 cartes, ≤4/id — feedback immédiat en UI)
│   └── state/                  # stores (Zustand/valtio — agnostique du rendu)
│       ├── decks.ts
│       ├── cosmetics.ts
│       └── sync.ts
├── adapters/                   # frontière plateforme — la SEULE zone qui varie
│   ├── platform.ts             # interface : FilePicker, DragDrop, Clipboard, Notify
│   ├── web.ts                  # <input type=file>, DataTransfer, navigator.clipboard
│   ├── tauri.ts                # dialog/fs de Tauri (accès direct aux fichiers du sim)
│   └── native.ts               # (futur RN/Tauri Mobile : DocumentPicker, share sheet)
├── components/                 # React « bête » : props in, events out, zéro logique
│   ├── AssetPackDropzone.tsx   # D&D d'un pack → adapters.DragDrop → core → API apply-pack
│   ├── DeckImportDialog.tsx    # collage/URL → core/decklist.ts → API import
│   ├── DeckLibrary.tsx         # liste, tags, état de sync (dirty/synced)
│   ├── CosmeticsGallery.tsx    # packs, aperçus, actif par profil
│   └── SyncStatusBadge.tsx
└── app/                        # assemblage Next.js (App Router) — remplaçable par
    │                           #   l'entry-point Tauri/RN sans toucher core/ ni components/
    ├── decks/page.tsx
    ├── cosmetics/page.tsx
    └── settings/page.tsx
```

## Décisions structurantes

1. **Le drag & drop de packs** (pilier 1) traverse `adapters/platform.ts` : sur web on ne
   reçoit que des `File` (pas de chemins) → upload vers le backend local qui applique ;
   sur Tauri on reçoit de vrais chemins → appel direct `apply-pack`. Le composant
   `AssetPackDropzone` ne connaît pas la différence.
2. **Pas d'accès direct au sim depuis le frontend** : tout passe par le backend Python
   (localhost) qui porte les garde-fous (whitelist, backups). Le frontend mobile parlera au
   même contrat d'API — sans pilier 1 (sandbox), il reste decks + cosmétiques (méta) + sync.
3. **`core/decklist.ts` duplique volontairement la validation Python** : le feedback de
   saisie doit être instantané (offline, avant tout aller-retour), et le backend revalide
   de toute façon (défense en profondeur).
4. **État = stores agnostiques** (pas de context React couplé au routing) : les mêmes
   stores s'importent dans Next.js, Tauri ou React Native.
```
