# productERP — Product Decisions & Design Notes

This is a living document. Every major product decision, deferred feature,
and design note is recorded here. This file should be updated as new
decisions are made. It serves as the source of truth for anyone working
on this project including AI coding assistants.

---

## Company & Brand Context

- **Tenant company name:** Drink Up Life, Inc
- **Brand 1:** Señor Sangria (premium bottled sangria, real fruit juice,
  no artificial ingredients)
- **Brand 2:** Backyard Barrel Co (placeholder for second brand)
- **Product:** productERP — a beverage industry operations platform
  built initially for Drink Up Life but designed as a multi-tenant SaaS
  product for other beverage companies

---

## Architecture Decisions

### Multi-Tenancy
- Multi-tenancy is implemented using a `company` foreign key on all
  relevant models
- No separate database schemas per tenant
- All queries must be scoped to the current tenant
- Future consideration: users that span multiple companies (noted,
  not in scope yet)

### Tech Stack
- Python / Django (latest stable)
- PostgreSQL
- Django templates + Bootstrap 5
- Deployed on Replit
- Environment variables for all secrets via Replit Secrets panel

### Mobile vs Desktop
- Mobile-first views: login, dashboard, user creation/management forms,
  event/field activity views, sales views for Sales Manager and
  Territory Manager
- Desktop-first views: reporting, admin, back-office features
- Base template uses Bootstrap 5 responsive grid with hamburger nav
  on mobile

---

## Data Model Decisions

### Company → Brand → Item Hierarchy
- A Company (tenant) can have multiple Brands
- A Brand can have multiple Items (SKUs)
- Each Item has two code fields:
  - **Item Code** — internal system code (example: Red0750)
  - **SKU Number** — external SKU number, optional/blank allowed

### Señor Sangria SKUs
| Name | Item Code |
|------|-----------|
| Classic Red 750ml | Red0750 |
| Classic Red 1.5L | Red1500 |
| Classic White 750ml | Wht0750 |
| Classic White 1.5L | Wht1500 |
| Spiked Red 750ml | SpkRed0750 |
| Spritz Red 12oz | SprRed12oz |
| Spritz White 12oz | SprWhite12oz |

### Distributors
- Distributor-brand relationship is derived from sales data,
  no explicit junction table exists
- A Distributor belongs to a Company (tenant)
- Cross-tenant distributor sharing is a future consideration,
  not in scope now

### Accounts
- Accounts represent physical retail locations
- A Distributor services an Account — the Account is not owned
  by the Distributor
- Accounts have a nullable FK to MasterAccount for future
  deduplication logic
- Account attributes: distributor, county, city,
  on-premise/off-premise, account type
- **Master Account / Golden Record concept:** When importing accounts
  from multiple distributors, the same physical location may appear
  under slightly different names or addresses
  (example: "Main Street Wine & Spirits" vs "Main Street W&S").
  A MasterAccount model is stubbed in as the canonical record.
  The logic to match and link distributor accounts to master accounts
  is deferred to a future phase.

### Events
- Events represent field activity: in-store tastings, festivals,
  special events, admin hours
- Any user in the company can be assigned as the ambassador
  on an event — role does not restrict field participation
- Recap and photo upload fields are deferred to Event Management phase

### Item Mapping
- Scoped to Brand (which is scoped to Company)
- If a sales data import contains an unrecognized item code,
  the import is aborted — nothing is written to the database
- The user is shown a clear list of unrecognized codes and told
  to create the items in Brand Management before re-importing
- Statuses: mapped, unmapped, ignored

---

## Role Hierarchy & Permissions

### Roles (highest to lowest)
1. **SaaS Admin** — platform level, manages all tenants,
   not scoped to any company
2. **Supplier Admin** — superuser within their Company,
   sees and does everything
3. **Sales Manager** — sees all distributors and accounts within
   their Company, can create events, assign anyone to events,
   correct work done by anyone below them
4. **Territory Manager** — same capabilities as Sales Manager
   but scoped to assigned accounts/territories, can create events
   and assign ambassadors within their scope
5. **Ambassador Manager** — assigned to specific accounts and
   ambassadors, can create events for their accounts and assign
   their ambassadors, no access to sales reports or financial data
6. **Ambassador** — scoped to their own assigned events only
7. **Distributor Contact** — read-only, scoped to their distributor,
   will view canned reports

### Authority Rule
Authority follows account assignment. If a Territory Manager is
assigned to an account, they own decisions for that account.
If no Territory Manager is assigned, the Sales Manager assigned
to that account is responsible.

### User Creation — Delegated Model
- Users can create other users at their level or below
- Users can only assign new users to accounts within their own scope
- Supplier Admin is the only role that can create other Supplier Admins
- Management chain: anyone above the creator in the chain can manage
  users created below them
- Example: Territory Manager creates an Ambassador → both the
  Territory Manager AND their Sales Manager can manage that Ambassador

### Field Participation
- Any user regardless of role can be assigned as the working
  ambassador on a tasting event
- This allows Supplier Admins, Sales Managers, and Territory Managers
  to work tastings and have admin hours tracked against them
- Event assignment dropdown defaults to users who have access to
  that specific account
- Supplier Admins and Sales Managers can toggle to see all company
  users when assigning events

---

## User Profile Fields
- First Name
- Last Name
- Email Address
- Phone Number
- Username
- Password
- Role
- Company (auto-assigned based on who creates them)
- Active flag

---

## Account Assignment to Users

### Two-Mode Assignment Interface (to be built in Phase 2)
**Mode 1 — Bulk assignment by attributes:**
Assign all accounts matching one or more of:
- Distributor
- County
- City
- On Premise / Off Premise

Filters can be combined (example: all off-premise accounts in
Bergen County under Distributor X)

**Mode 2 — Individual account selection:**
Searchable list for one-off or exception assignments

### Dynamic vs Static Assignment
- Phase 2 will implement static assignment
- Dynamic assignment (new imported accounts automatically inherit
  user assignments based on attribute rules) is deferred to
  a future phase

---

## Data Imports

### VIP Import (Distributor Sales & Inventory Data)
- VIP is a third-party platform where all distributors connect
  their back-end systems
- Import timing is ad hoc (not scheduled)
- Report format is consistent — pre-configured in VIP,
  user just changes the date
- Sometimes exports are limited to one distributor at a time —
  the import tool must allow the user to tag which distributor
  a file belongs to before importing
- Both sales data and inventory data come from VIP
- Sample CSV files will be provided when this feature is built

### Import Behavior
- If unrecognized item codes are present, the import is aborted
  and nothing is written to the database
- If any sale date in the file already exists for that distributor,
  the import is aborted and nothing is written to the database
- The user receives a clear error message in both cases
- A clean file with all items pre-mapped is required before
  an import will be accepted

### Account Import
- Account lists also come from distributors
- Field definitions will be refined when sample CSV files
  are provided

---

## QuickBooks Integration
- No QuickBooks integration will be built
- Bookkeeper manually enters data into QB from productERP exports
- productERP needs to produce clean exports and reports
  that can be emailed to the bookkeeper

---

## Sales Orders
- Distributors send POs in various formats — some email orders
  with made-up PO numbers
- Manual entry of sales orders will remain (no automation)
- One entry generates three outputs:
  1. Warehouse copy
  2. Freight company copy
  3. Bookkeeper export (for QB entry)

---

## Tasting Agencies (Deferred)
- A Tasting Agency functions like an Ambassador Manager but
  operates as their own company/tenant
- Their ambassadors could span multiple tenants
- When introduced, Territory Managers and Sales Managers will
  have NO management rights over agency staff —
  it is a contracted service relationship
- **This is explicitly out of scope until a future phase**

---

## Phase Plan

| Phase | Description | Status |
|-------|-------------|--------|
| Foundation | Project setup, data models, admin, seed data | ✅ Complete |
| Phase 1 | Login, User Accounts, Roles | ✅ Complete |
| Phase 2.1 | Brand & Item Management, Distributor Management | ✅ Complete |
| Phase 2.2 | Sales Data Import, Item Mapping, Batch History | 🔄 In Progress |
| Phase 2.3 | Account Conflict Detection & Merge Tool | ⬜ Pending |
| Phase 2.5 | Manual Account Creation | ⬜ Pending |
| Phase 3 | Sales Views | ⬜ Pending |
| Phase 4 | Saving Sales Views | ⬜ Pending |
| Phase 5 | CRM — Accounts (contacts, notes) | ⬜ Pending |
| Phase 6 | Sales Reports / Distributor Reports | ⬜ Pending |
| Phase 7 | Sales Orders | ⬜ Pending |
| Phase 8 | Production Ordering | ⬜ Pending |
| Phase 9 | Projection Planning | ⬜ Pending |
| Phase 10.1 | Account Assignment & Ambassador Coverage Areas | ⬜ Pending |
| Phase 10.2 | Event Scheduling & Status Workflow | ⬜ Pending |
| Phase 10.3 | Event Recap | ⬜ Pending |
| Phase 10.4 | Expense Management | ⬜ Pending |
| Phase 10.5 | Event Export | ⬜ Pending |

---

## Phase 1 — Completed Features

### Authentication
- Branded productERP login page, mobile-first
- Case insensitive username login
- Role-based redirect after login
- Logout redirects to login page
- Forgot password link stubbed

### Role-Based Dashboard
- Single adaptive dashboard template that changes by role
- Welcome message with user first name, role, and company name
- Role-appropriate navigation for all 7 roles
- Hamburger nav on mobile
- Placeholder pages for features not yet built

### User Management
- Delegated user creation — users can create roles at or
  below their own level
- Role dropdown defaults to "Select Role" blank option
- Company auto-assigned based on creator's company
- User list view with search and filter by name or role
- Mobile-optimized user list — role and status displayed
  under email address to eliminate horizontal scrolling
- Edit user — all fields except password
- Deactivate/reactivate user
- Password change as separate action
- Supplier Admin and Sales Manager can reset other
  users' passwords

### Access Control
- All pages require authentication
- Role-based access enforced on every view
- Friendly "Access Denied" page for unauthorized access
- Tenant scoping enforced — users only see their
  company's data

### My Profile
- View and edit own information
- Change own password

### UI Notes
- Tagline updated to "Product Operations Platform"
  across all templates
- Username stored and matched in lowercase throughout

---

## Deferred Features (Not In Current Scope)
- Dynamic account assignment (new imports auto-inherit user assignments)
- Cross-tenant distributor sharing
- Multi-company user accounts (users spanning multiple tenants)
- Tasting Agency as separate tenant/company
- Master Account matching logic (golden record deduplication)
- Profile photos on user accounts
- Mobile app (native) — current approach is responsive web

### Distributor CRM (Future)
- Distributors will eventually have contacts associated
  to them — people we interact with regularly such as
  portfolio managers and distributor sales managers
- This CRM feature for distributors should be built at
  the same time as the CRM features for accounts (Phase 5)
- Contact fields will include at minimum: name, title,
  email, phone

### Distributor Sales Rep Tagging (Future)
- Over time we will learn which distributor sales reps
  are responsible for which retail accounts
- The ability to tag a distributor sales rep to an
  account will allow us to generate reports by rep
- This data will help identify ways to help those reps
  improve sales performance in their accounts
- This feature should be considered when building
  account-level CRM and reporting features

---

## Open Questions & Future Considerations
- When a user spans multiple companies (future), how is the
  company context switched after login?
- When Tasting Agencies are introduced, what is the contractual
  and data relationship between the agency tenant and the
  brand tenant?
- Should new imported accounts automatically inherit user
  assignments based on attribute rules (dynamic assignment)?

---

---

## Phase 2.1 — Completed Features

### Brand Management (Supplier Admin only)
- Brand list view: name, item count, active status, edit/deactivate actions
- Brand create/edit form: name (required, unique within company), description, active flag
- Brand detail page: brand info + all items with inline actions
- Company auto-assigned from logged-in user

### Item Management (within Brand context)
- Item list within brand detail: name, item code, SKU, active status, edit/deactivate
- Item create/edit form: name, item code (unique within brand), SKU number (optional), description, active
- Existing seed data (Señor Sangria, Backyard Barrel Co) fully editable through these interfaces

### Distributor Management (Supplier Admin only)
- Distributor list view with name search: name, city, state, active status, view/edit/deactivate
- Distributor create/edit form: name (required, unique within company), address, city, state, notes, active
- Distributor detail page: full info, accounts list (placeholder until Phase 2.2), import history (placeholder)
- Distributor model extended with city, state, notes fields (migration 0002)
- No brand-distributor junction table created (relationship derived from sales data in future phase)

### Navigation Updates
- Supplier Admin sidebar: Brands and Distributors are now live links (removed "Soon" badges)
- Mobile nav updated to match

---

## Phase 2 — Design Decisions

### Brand & Item Management
- Full CRUD for Brands and Items
- Supplier Admin only
- Item Code must be unique within a Brand
- Existing seed data (Señor Sangria, Backyard Barrel Co)
  is editable through these interfaces

### Distributor Management
- Full CRUD for Distributors
- Supplier Admin only
- No explicit brand-distributor association table
- Brand-distributor relationship is derived from
  sales data, not stored as a separate record

### Sales Data Import (formerly referred to as
VIP Import — renamed to reflect that the format
is platform-agnostic)
- Import type is called "Sales Data Import"
- Supplier Admin only
- Distributor is selected before file upload
- Performance approach: bulk_create in batches
  of 500-1000 rows, all account matching done
  in memory, single database transaction
- Expected volume: 5,000 to 30,000 records per file
- Historical data: 2-3 years imported across
  multiple files, oldest to newest
- One distributor at a time

### Account Unique Identifier (Composite Key)
- Unique key for account matching during import:
  Normalized Address + City + State
- Normalization means: uppercase, trimmed,
  standardized abbreviations
  (Street→ST, Avenue→AVE, etc.), no punctuation
- Account Name is intentionally excluded from
  the composite key because names change over time
- Name changes are handled by the Account Conflict
  Detection tool (Phase 2.3)

### Normalized Address Storage
- Normalized address values are stored as separate fields
  on the Account model, not calculated on the fly
- Fields to add: address_normalized, city_normalized,
  state_normalized
- Original fields (address, city, state) are preserved
  exactly as received for display purposes
- Normalized fields are used only for matching and
  conflict detection
- Normalization rules: uppercase, trimmed whitespace,
  standardized abbreviations (Street→ST, Avenue→AVE,
  Boulevard→BLVD, Drive→DR, Road→RD, Lane→LN,
  Court→CT, Place→PL), punctuation removed

### Duplicate Import Detection
- If any sale date in the incoming file already
  exists in SalesRecord for that distributor,
  stop and abort — nothing written to database
- Show clear error message identifying
  conflicting dates

### Import Abort on Unknown Item Codes
- If any Item Name ID in the file does not have
  an existing ItemMapping record for that
  distributor, abort the import
- Show clear message listing unrecognized codes
- Tell user to create items in Brand Management
  first then re-import
- Nothing is written to the database on abort

### Account Auto-Creation on Import
- Accounts are auto-created from sales data
  if no matching record exists
- Matching uses normalized Address + City + State
- Auto-created account fields: Name, Address,
  City, State, Zip, Distributor, VIP Outlet ID
  (reference only), County (or "Unknown"),
  On/Off Premise (or "Unknown")
- Auto-created flag set to True
- Separate account records created per distributor
- Master Account matching deferred to Phase 2.3

### Merged Account Records
- When accounts are merged in Phase 2.3, the
  older duplicate record is kept but flagged
  as merged
- A merged_into foreign key on Account points
  to the master record
- A note field captures the reason for the merge
- All report queries must exclude merged records
- An active_accounts model manager will be built
  to automatically filter out merged records so
  report writers don't need to handle this manually
- Historical sales from merged accounts are
  attributed to the master account in reporting

### Account Conflict Detection Tool (Phase 2.3)
- Separate tool, not part of import process
- Scans all accounts for potential duplicates
  using fuzzy matching at 80% similarity threshold
- Flags conflicts where same address but
  different name, or same name but different address
- Supplier Admin reviews each conflict and can:
  * Merge (enter a note explaining why)
  * Keep Separate (won't appear again)
  * Ignore for now
- On merge: older record flagged as merged,
  master_account FK populated, note saved
- Import historically oldest to newest so
  newer records become the canonical version

### Batch Import History
- Supplier Admin only (Sales Managers cannot
  view import history at this time)
- Shows all imports with: date, distributor,
  data date range, records imported, accounts
  created, status
- Delete batch with safe rollback
- Safe delete: only removes auto-created accounts
  with no other batch references
- CRM data deleted along with account if removed
  during rollback

---

## Phase 10 — Event Management Design Decisions

### Phase Breakdown
- Phase 10.1 — Account Assignment & Ambassador Coverage Areas
- Phase 10.2 — Event Scheduling & Status Workflow
- Phase 10.3 — Event Recap
- Phase 10.4 — Expense Management
- Phase 10.5 — Event Export
- Phase 2.5 — Manual Account Creation
  (inserted before Phase 10 work begins)

### Photo Storage
- Photos are stored in object storage, not the database
- File URL is stored in the database
- Development: Django local file storage
- Production: Cloudflare R2 (S3-compatible, zero egress fees)
- Swap is a single settings change, no code rewrite needed

### Event Types
Three event types, each drives different behavior:

1. Tasting — full recap required, account required,
   items selection required
2. Festival — simplified recap (comment box + expenses),
   account required
3. Admin — no recap, no account required, captures hours
   for compensation purposes

### Event Creation
Who can create events:
- Supplier Admin
- Sales Manager
- Territory Manager
- Ambassador Manager

Event setup fields (set by creator, not ambassador):
- Event Type (selected first, drives remaining fields)
- Account — required for Tasting and Festival,
  not required for Admin
- Date
- Duration — hours selector + minutes selector
  (used for bookkeeper compensation export)
- Items to be sampled — Tasting only, multi-select from
  items associated to the distributor that services the
  account (derived from sales data)
- Ambassador — filtered dropdown showing only ambassadors
  whose coverage area overlaps with the event account
- Event Manager — defaults to person creating the event,
  can be reassigned to any TM or AM who has that account
  in their territory

### Ambassador Role Clarification
- Ambassadors do NOT own accounts
- Ambassadors have a personal coverage area on their
  profile defining where they are willing to travel
- Coverage area is defined by any combination of:
  states, counties, cities, distributors, specific accounts
- Ambassador dropdown for events filters based on coverage
  area overlap with the event account
- Ambassadors only fill out recap information — they do not
  create or edit event setup fields

### Ambassador Manager Role Clarification
- AM does not visit stores
- AM coordinates tastings remotely between stores and ambassadors
- AM reviews sales reports and works with distributors to
  identify tasting opportunities
- AM can assign themselves as the working ambassador on an event
  (no special flag needed — this is handled naturally by the role)
- AM is assigned to accounts/areas similar to Territory Manager

### Territory Manager Role Clarification
- TM visits stores physically
- TM builds brand awareness and manages relationships with
  store managers
- TM can book events and can also hand off coordination to an AM
- TM sees ALL events at accounts in their territory regardless
  of who created the event
- Visibility is driven by account assignment, not by who booked
  the event

### Event Status Workflow
Five statuses in order:

1. Draft — event is being set up, not yet visible to ambassador.
   Creator is still coordinating with account.
2. Scheduled — event released, now visible to assigned ambassador
3. Recap Submitted — ambassador has completed and submitted
   recap information
4. Complete — event creator has reviewed recap and marked event
   as complete

Admin events follow a simpler flow:
Draft → Scheduled → Complete (no recap step)

Festival events follow:
Draft → Scheduled → Recap Submitted → Complete

### Event Permissions
- Event setup fields: editable by AM, TM, Sales Manager,
  Supplier Admin
- Recap fields: editable only by the assigned Ambassador
- TM sees all events at accounts in their territory regardless
  of who created them
- AM sees all events they created or manage
- Ambassador sees only their assigned events
- Admin events: visible to creator and anyone above them in
  role hierarchy, no account scoping

### Event Recap by Type

Tasting recap — 3 parts:

Part 1 — Overall Event:
- Number of samples poured
- Number of QR codes scanned
- General notes (free text)
- One or more photos

Part 2 — Per Item Sampled:
- For each item selected at event setup:
  * Shelf price
  * Bottles sold
  * Bottles used for samples

Part 3 — Expenses:
- One or more expense entries
- Each entry: description, amount, receipt photo
- No approval workflow required

Festival recap:
- Comment box only
- Expenses (same structure as tasting)

Admin:
- No recap

### Event Export (Phase 10.5)
- Not called compensation export — called Event Export
- Supplier Admin selects a date range
- Export lists all events in that range with: account,
  event type, ambassador, duration, expenses
- Output provided to bookkeeper who handles compensation
  processing externally
- Format TBD when building Phase 10.5

### Account Assignment (Phase 10.1)
- Applies to: Territory Managers and Ambassador Managers
- Does NOT apply to Ambassadors (they have coverage areas instead)
- Many-to-many relationship: multiple TMs or AMs can be assigned
  to the same account, one TM or AM can be assigned to many accounts
- Two assignment modes:
  Mode 1 — Bulk by attributes (distributor, county, city,
  on/off premise, combinable)
  Mode 2 — Individual account selection (searchable list
  for exceptions)
- Static assignment for now, dynamic assignment deferred

### Ambassador Coverage Area (Phase 10.1)
- Stored on ambassador's user profile
- Defined by any combination of: states, counties, cities,
  distributors, specific accounts
- Used only for filtering event assignment dropdown —
  not for ownership or authority
- Many-to-many relationships needed for each geographic dimension

### Manual Account Creation (Phase 2.5)
- Lightweight form to manually create an account when it doesn't
  exist in the system
- Used when a new account needs an event before sales data has
  been imported
- Fields: name, address, city, state, zip, distributor, county,
  on/off premise
- auto_created flag set to False (manually created)
- Merging manually created accounts with later-imported accounts
  is deferred to a future phase

### Tasting Agency (Reminder — Deferred)
- Agencies are third-party Ambassador Managers
- Explicitly out of scope until a future phase
- The AM role and structure should be designed with agency
  introduction in mind

---

## Deferred Features — Additions

### Active Accounts Model Manager
- Built in Phase 2.2 (ahead of Phase 2.3 schedule)
- active_accounts custom manager on the Account model
- Automatically excludes merged (merged_into__isnull=False)
  and inactive (is_active=False) accounts from all queries
- All report and display queries must use this manager
  rather than the default objects manager

---

## Phase 2.2 — Improvements

### Default Distributor on Re-Import
- After a successful import, the "Import Another File" button passes
  the distributor ID as a URL parameter (`?distributor=<pk>`)
- The upload form view reads this parameter and pre-selects the
  distributor in the dropdown via Django's `initial` dict on the form
- Eliminates the need to re-select the same distributor when importing
  multiple files from the same distributor in sequence

### Item Mapping List — Mapped To Column
- The "Mapped To" column now shows the productERP item name on the
  first line and the item code below it in `<small class="text-muted">`
- Replaces the previous inline parenthetical format

### Import History Monthly View
- Import History page now has two tabs: List View (unchanged) and
  Monthly View
- Monthly View shows a grid: rows = all active distributors for the
  company, columns = 12 months for the selected year
- Year tabs above the grid, built dynamically from ImportBatch data;
  most recent year shown first and selected by default
- Each cell shows records_imported as a clickable link to the batch
  detail page; multiple batches in a month each get their own link
- All batches for the year fetched in a single query, organized into
  a dict in Python (no per-cell queries)
- Distributors with no data for the year still appear as rows

*Last updated: February 25, 2026*
*Maintained by: Drink Up Life, Inc / productERP project team*
