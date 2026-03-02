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

### Environment Configuration & Deployment
- All environment-specific configuration is managed via standard environment
  variables. No deployment-platform-specific APIs or patterns are used anywhere
  in the codebase.
- The platform is portable to any hosting provider (Replit, Render, AWS, or
  other) without code changes.
- Required environment variables are documented in DEPLOYMENT.md in the
  project root.

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
| Phase 2.5 | Manual Account Creation | ✅ Complete |
| Phase 3 | Sales Views | ⬜ Pending |
| Phase 4 | Saving Sales Views | ⬜ Pending |
| Phase 5 | CRM — Accounts (contacts, notes) | ⬜ Pending |
| Phase 6 | Sales Reports / Distributor Reports | ⬜ Pending |
| Phase 7 | Sales Orders | ⬜ Pending |
| Phase 8 | Production Ordering | ⬜ Pending |
| Phase 9 | Projection Planning | ⬜ Pending |
| Phase 10.1 | Account Assignment & Ambassador Coverage Areas | ✅ Complete |
| Phase 10.2 | Event Scheduling & Status Workflow | ✅ Complete |
| Phase 10.3.1 | Event Detail UI Reorganization & Admin Event Flow Fix | ✅ Complete |
| Phase 10.3.2 | Account-Item Association (models + import) | ✅ Complete |
| Phase 10.3.3 | Event Recap Form (Tasting + Festival) | ✅ Complete |
| Phase 10.3.3 Tweaks | Festival→Special Event, Sort Order, Revert Complete, Account Mgmt | ✅ Complete |
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

### Item Sort Order
- Item has a sort_order field (PositiveIntegerField, default=0)
- Sort order is per-brand — each brand maintains its own sequence
- Items are sorted by brand name first, then by sort_order within
  each brand, in all display contexts:
  * Event Detail items list
  * Recap form per-item section
  * Event Create items multi-select optgroups
  * Account Detail items display
- Brand management UI shows a Sort Order column with up/down arrow
  buttons for AJAX reordering without page reload
- First item in a brand's list has no up arrow; last has no down arrow

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
2. Special Event — simplified recap (comment box + expenses),
   account required. Internal model choice value is 'special_event'.
   Previously called 'Festival' (internal value 'festival'); renamed
   to Special Event and all existing 'festival' values migrated.
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
Six statuses in order:

1. Draft — event is being set up, not yet visible to ambassador.
   Creator is still coordinating with account.
2. Scheduled — event released, now visible to assigned ambassador
3. Recap In Progress — ambassador has started but not yet submitted the recap;
   set automatically on first recap save
4. Recap Submitted — ambassador has completed and submitted
   recap information
5. Revision Requested — Event Manager or above found issues in
   the recap; revision_note field captures what needs to be fixed
6. Complete — event creator has reviewed recap and marked event
   as complete

### Revert Completed Events
- Completed events can be reverted to Recap Submitted by Supplier Admin,
  Sales Manager, and the assigned Event Manager on that specific event
- For all event types (Tasting, Special Event, Admin): Complete → Recap Submitted
- Uses a confirmation modal before executing the revert
- Endpoint: POST /events/<id>/revert-complete/
- After revert, redirects to the event detail page with a success message

Admin events follow a simpler flow:
Draft → Recap Submitted → Complete (no recap step, no Scheduled)

Tasting and Festival events follow:
Draft → Scheduled → Recap In Progress → Recap Submitted → Complete

Unlock behavior: Recap Submitted → Recap In Progress (not back to Scheduled)

Badge color for Recap In Progress: bg-warning text-dark (yellow/amber)

### revision_note Field
- Added to Event model in Phase 10.2
- TextField, blank=True
- Populated by Event Manager / Sales Manager / Supplier Admin
  when requesting revision on a Recap Submitted event
- Displayed prominently in a highlighted alert box on the event
  detail page when status = Revision Requested
- Ambassador sees this note so they know what to fix in their recap

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

### Universal Account Visibility Rule (Final)
- SaaS Admin: sees all accounts across all companies, no filtering
- Supplier Admin: sees all accounts for their company, no coverage area filtering
- All other roles including Sales Manager, TM, AM, Ambassador: coverage area
  filtering applies
- Zero accounts shown if no coverage areas assigned (with explanatory message)
- get_accounts_for_user() in accounts/utils.py is the single source of truth
  for this logic and must be used consistently everywhere

### User Management Access
- Only Supplier Admin and SaaS Admin can create, edit, and manage users
- Only Supplier Admin and SaaS Admin can see the Users area in navigation
- All other roles have no user management access

### Imported Account Editing
- Accounts created by sales data import (auto_created=True) cannot be manually edited
- Edit button hidden in list and detail views
- Server-side guard prevents direct URL access to edit page for imported accounts
- Explanatory note shown on detail page

### Account Deletion
- Only manually created accounts (auto_created=False) can be deleted
- Before deleting, check for associated data: events, AccountItem records,
  EventPhoto records
- If any associated data exists, deletion is blocked with a clear error message
  listing what data is blocking it
- If no associated data exists, deletion requires confirmation modal
- Imported accounts (auto_created=True) do not get a delete option

### Account Deactivation
- Any account (manual or imported) can be deactivated regardless of associated data
- Deactivation sets the account to inactive (is_active=False)
- A deactivated account can be reactivated from the same detail page
- Button label toggles between "Deactivate" and "Reactivate" based on current status
- Confirmation modal required before deactivating

### Sales Import — Inactive Account Reactivation
- If an inactive imported account appears in a sales import, it is automatically
  reactivated (is_active set to True)
- The ImportBatch summary logs a count of accounts_reactivated
- The batch detail page surfaces reactivated accounts alongside auto-created accounts

### Admin Event Rules
- Start time not captured for admin events
- Event Manager always set to creator for admin events, field not shown
- Duration (hours + minutes) is the only time-related field for admin events

### Ambassador Dropdown Roles
- Ambassador, AM, TM, Sales Manager filtered by coverage area
- Supplier Admin appears for all events regardless of coverage area
- SaaS Admin and Distributor Contact excluded from ambassador dropdown

### Event Manager Rules (Updated)
- Event Manager dropdown includes same roles as Ambassador dropdown:
  AM, TM, Sales Manager (coverage-filtered), Supplier Admin (always included)
- Ambassador role itself is excluded from Event Manager dropdown
- Event Manager defaults to the event creator for all roles
- For Admin events: Event Manager is always the creator, field not shown
- Recap approval authority: Event Manager OR any role above them in hierarchy

### Event Deletion Rules
- Only Draft events can be permanently deleted
- Deletion requires confirmation modal (Bootstrap modal, POST only)
- Access: Event Manager role and above (AM, TM, Sales Manager, Supplier Admin)
- Once released (Scheduled or beyond) events can only be moved back to Draft
  first, then deleted if needed

### Move Back to Draft
- Scheduled events can be moved back to Draft status
- Endpoint: POST /events/<id>/unrelease/
- Access: same as Release action (AM, TM, Sales Manager, Supplier Admin)
- Events beyond Scheduled status cannot be moved back to Draft

### Ambassador Dropdown — Event Type Behavior
- Tasting and Festival events: Ambassador dropdown is empty on page load until
  an account is selected via live search; placeholder text guides the user
- Admin events: Ambassador dropdown is populated immediately on page load with
  all eligible company users (no account required for Admin events)

### Login Redirect — AM and Ambassador
- Ambassador Manager and Ambassador roles are redirected to /events/ after login
- Direct navigation to /dashboard/ also redirects these roles to /events/
- All other roles use existing dashboard redirect

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

---

## Phase 2.4 — App Structure & Data Model Cleanup

### New Apps: accounts, sales
- `apps.accounts` — owns `Account` and `UserCoverageArea` models
- `apps.sales` — owns `SalesRecord` model
- Both registered in `INSTALLED_APPS` after `apps.distribution`

### Account Moved: distribution → accounts
- `Account` model moved from `apps.distribution` to `apps.accounts`
- `master_account` FK to `MasterAccount` removed (MasterAccount was unused)
- All FKs updated: `events.Event.account` now points to `accounts.Account`
- `imports/views.py` now imports `Account` from `apps.accounts.models`
- Migration path: create `accounts.Account` → update `events.Event.account` FK →
  delete `distribution.Account`; data preserved (table had 0 records at migration time)

### MasterAccount Removed
- `MasterAccount` model removed from `apps.distribution`
- Was a stub for future deduplication; never populated; no data loss
- Deduplication concept will be revisited if needed; `Account.merged_into`
  self-FK already provides merge support

### Coverage Area Union Logic
- Coverage area assignments use union logic
- A user sees ALL accounts that match ANY of their coverage area entries combined
- Example: Distributor X + City Hoboken = all accounts under Distributor X PLUS
  all accounts in Hoboken (regardless of distributor)
- Sets are always combined (union), never intersected (AND)
- This applies to all roles that use coverage areas: TM, AM, Sales Manager, Ambassador

### UserCoverageArea — Replaces User M2M Territory Fields
- New model `accounts.UserCoverageArea` replaces the removed M2M fields on `User`
- Removed from `User`: `territory` (CharField), `assigned_distributors` (M2M),
  `assigned_accounts` (M2M), `managed_ambassadors` (M2M)
- `UserCoverageArea` is flexible: coverage_type choices are distributor, state,
  county, city, account — supports all current and planned assignment patterns
- M2M junction tables dropped: `core_user_assigned_accounts`,
  `core_user_assigned_distributors`, `core_user_managed_ambassadors`
- UserCoverageArea not yet wired to any view logic; structure only in this phase

### SalesRecord Moved: imports → sales
- `SalesRecord` model moved from `apps.imports` to `apps.sales`
- `account` FK updated to point to `accounts.Account` (not `distribution.Account`)
- `imports/views.py` now imports `SalesRecord` from `apps.sales.models`
- Migration path: create `sales.SalesRecord` → delete `imports.SalesRecord`;
  data preserved (table had 0 records at migration time)

### Distributor Cleaned Up
- Removed from `Distributor`: `brands` (M2M to catalog.Brand), `email`, `phone`
- `brands` M2M was unused; brand-distributor relationships will be modeled
  differently when needed (not current scope)
- `distribution_distributor_brands` junction table dropped

---

## Phase 2.5 — Completed Features

### Manual Account Creation
- Account list view at `/accounts/` — accessible to Territory Manager,
  Ambassador Manager, Sales Manager, and Supplier Admin
- Search by account name or city; filter by distributor, on/off premise,
  and source (manual vs. imported)
- Account detail view at `/accounts/<id>/` — read-only display of all
  account fields; placeholder sections for Events, Sales History, CRM Notes
- Create account form at `/accounts/create/` — all editable fields;
  auto_created set to False; normalized fields populated on save
- Edit account form at `/accounts/<id>/edit/` — pre-populated; normalized
  fields re-computed on save
- Deactivate / Reactivate via POST from the detail page; no separate page
- All views scoped to the logged-in user's company
- active_accounts manager used for list view (excludes merged and inactive)
- normalize_address applied to street, city, state on every save
- Mobile-friendly: list collapses to name, city/state, and source badge
  on small screens; full columns visible on lg+ viewports

### Navigation Updates
- Supplier Admin: Accounts link added to Operations section in sidebar
  and mobile nav
- Sales Manager: Accounts placeholder replaced with live link in sidebar
  and mobile nav
- Territory Manager: Accounts placeholder replaced with live link in
  sidebar and mobile nav
- Ambassador Manager: Accounts link added above Events placeholder in
  sidebar; Accounts link added to mobile nav

---

## Phase 10.1 — Completed Features

### Coverage Areas on User Profiles

- Tabbed User Edit page: Profile tab (existing, unchanged) and Coverage Areas tab
- Coverage Areas tab visible to Supplier Admin only; all other roles see the Profile
  tab only with no tab UI
- After saving the Profile form the user is redirected back to the same Edit page
  (keeping the user in context), replacing the previous redirect to the user list

### Coverage Areas Tab — Current Assignments

- Table showing all UserCoverageArea records for the target user
- Columns: Type (color-coded badge), Value, State (for county/city types), Action
- Empty state message when no coverage areas are assigned yet
- Inline Remove confirmation: clicking Remove shows "Remove [Type]: [Value]?
  Confirm / Cancel" in the same row; no page navigation required

### Coverage Areas Tab — Add Coverage Area Form

- Coverage Type dropdown with five options: Distributor, State, County, City, Account
- Form is always visible below the assignments table; resets after each successful
  addition so multiple entries can be added without extra navigation
- **Distributor type**: dropdown of all active distributors for the company
- **State type**: dropdown populated via AJAX from distinct `state_normalized` values
  in the company's active accounts (same pattern as County and City); shows
  "No states available yet" message if no account data exists
- **County type**: state dropdown first; county dropdown populated via AJAX when
  state is selected; message shown if no counties exist for that state yet
- **City type**: state dropdown first; city dropdown populated via AJAX when state
  is selected; message shown if no cities exist for that state yet
- **Account type**: live search box (triggers after 2+ characters with 300 ms
  debounce); results show account name, address, distributor; each result has an
  inline Add button; search box clears after adding; no separate Add button for
  this type

### AJAX Endpoints (accounts app)

- `GET /accounts/ajax/counties/?state=NJ` — distinct counties for the company and
  state, excludes blank and "Unknown" values
- `GET /accounts/ajax/cities/?state=NJ` — distinct cities for the company and state
- `GET /accounts/ajax/search/?q=barrel` — active accounts matching name, street,
  city, or state (max 20 results); returns id, name, street, city, state, distributor
- All endpoints require authentication and return 403 if unauthenticated
- Coverage area add/remove endpoints require Supplier Admin role

### Data & Validation

- Duplicate prevention: adding the same type + value combination twice for the same
  user is blocked with an inline error message
- Account search uses the active_accounts manager (excludes inactive and merged)
- County and city queries use state_normalized for accurate state matching
- US States constant list (`apps/accounts/constants.py`) used across forms and display
- State stored as 2-letter abbreviation; full name shown in assignments table via
  US_STATES_DICT lookup computed in Python (no custom template filters needed)

### JavaScript

- Vanilla JavaScript only (no jQuery or additional libraries)
- Event delegation used for dynamically-rendered table rows (Remove/Confirm/Cancel
  buttons survive AJAX table re-renders)
- CSRF token read from cookie for fetch() calls
- Add and Remove return rendered table HTML in JSON response; table container
  replaced in-place without full page reload

---

## Phase 10.2 — Completed Features

> ✅ **Complete and tested.** All features in this phase have been built,
> verified in the running application, and pushed to GitHub.

### Event Model
- EventType choices: Tasting, Festival, Admin
- Status choices: Draft, Scheduled, Recap Submitted, Revision Requested, Complete
- Fields: company, event_type, status, account (nullable for Admin events),
  date, start_time, duration_hours, duration_minutes, ambassador, event_manager,
  created_by, items (M2M to catalog.Item), notes, revision_note
- duration_display property returns human-readable string ('2h 30m', '1h', etc.)
- status_badge_class property returns Bootstrap badge color class

### Coverage Area Utilities (apps/accounts/utils.py)
- get_accounts_for_user(user) — returns queryset of active accounts visible to
  a user based on their UserCoverageArea records (union logic); Supplier Admin
  and Sales Manager see all company accounts
- get_users_covering_account(account, roles) — returns users with given roles
  whose coverage areas include the given account; used for ambassador and
  event manager dropdown filtering on event create/edit forms

### Event List (/events/)
- Access: all roles except Distributor Contact
- Status group ordering: Revision Requested → Draft → Recap In Progress →
  Recap Submitted → Scheduled → Complete
- Revision Requested and Draft groups highlighted with light red background;
  Recap In Progress also gets light red background (all three require user action)
- Revision Requested retains its red left border in addition
- Section header rows between status groups
- Mobile card layout; desktop table layout
- Collapsible filter bar with session persistence (key: 'event_list_filters');
  filters restored on return visit, cleared via Clear Filters button
- Filters: status (multi-select), year, month, event type, creator,
  distributor, account name, city
- "Filters Active" badge when any filter is applied
- Ambassadors do not see Draft events
- "New Event" button opens a Bootstrap modal prompting the user to select
  event type before proceeding to the create form

### Event Detail (/events/<id>/)
- Shows all event fields; items displayed grouped by brand for Tasting events
  (brand name as a subtle muted header, item names listed below — no item code)
- Status-appropriate action buttons: Release, Approve & Complete, Request Revision,
  Move Back to Draft, Delete Event
- Revision note displayed in a highlighted alert box when status = Revision Requested
- Request Revision and Delete use Bootstrap confirmation modals
- Role-based access: all viewer roles can view; action buttons visible to
  Event Manager role and above

### Event Create (/events/create/)
- Event type selected via Bootstrap modal on the event list page; three buttons —
  Tasting, Festival, Admin — each link to /events/create/?type=<value>
- type URL parameter validated server-side; navigating without a valid type
  redirects back to /events/
- Event type rendered as a read-only badge on the form — cannot be changed
  once selected; a hidden input submits the value with the form
- Cancel button redirects to /events/ (event list)
- Account selected via live search (debounced, 2+ character trigger); selected
  account displayed inline with a Clear option
- Selecting an account triggers AJAX refresh of ambassador and event manager
  dropdowns filtered by coverage area
- Items multi-select (Tasting only) uses HTML optgroup elements to group options
  by brand; only item name shown, no item code
- created_by = logged-in user; event_manager defaults to creator for all roles
  if not explicitly set; status always starts as Draft

### Event Edit (/events/<id>/edit/)
- Event type shown as read-only badge — event type cannot be changed after
  creation; a hidden input submits the locked value with the form
- All other fields editable per the same rules as event create
- Items multi-select groups by brand using optgroup, pre-selected items
  highlighted on page load

### Admin Event Rules
- Account field hidden; not required for release
- Start time field hidden
- Event manager always set to creator on save; event manager field not shown
- Ambassador dropdown populated from all company users on page load
  (no account-based filtering required)
- Sales Manager visibility: all admin events in the company are visible
  to Sales Managers (no account scoping — per spec, admin events are
  visible to creator and anyone above them in the role hierarchy)

### AJAX Endpoints
- GET /events/ajax/ambassadors/?account_id=X — ambassadors, AMs, TMs, Sales
  Managers, and Supplier Admins covering the given account; all company users
  in those roles returned when no account_id provided (Admin events)
- GET /events/ajax/event_managers/?account_id=X — AMs, TMs, Sales Managers,
  Supplier Admins covering the account; all returned when no account_id provided
- GET /events/ajax/accounts/?q=<term> — live account search, filtered through
  user's coverage areas via get_accounts_for_user(); max 20 results
- All endpoints require authentication

### Status Transitions
- POST /events/<id>/release/ — Draft → Scheduled; validates date, ambassador,
  and account (account not required for Admin)
- POST /events/<id>/unrelease/ — Scheduled → Draft (Move Back to Draft)
- POST /events/<id>/save-recap/ — saves recap data; moves Scheduled → Recap In
  Progress on first save; stays in Recap In Progress on subsequent saves
- POST /events/<id>/submit-recap/ — saves recap data and moves status to
  Recap Submitted; validates minimum required fields
- POST /events/<id>/unlock-recap/ — Recap Submitted → Recap In Progress; available
  to Ambassador, Event Manager, and coverage-area users
- POST /events/<id>/request-revision/ — Recap Submitted → Revision Requested;
  requires revision_note explaining what needs fixing
- POST /events/<id>/approve/ — Recap Submitted → Complete; includes race condition
  guard that verifies status is still Recap Submitted at moment of approval
- POST /events/<id>/delete/ — Permanently deletes Draft events only; requires
  Bootstrap confirmation modal before submitting
- All transitions accessible to AM, TM, Sales Manager, Supplier Admin

### Login Redirect — AM and Ambassador
- Ambassador Manager and Ambassador roles redirect to /events/ after login
- Direct navigation to /dashboard/ also redirects these roles to /events/
- All other roles use the standard dashboard redirect

### Navigation
- Events link added to sidebar and mobile nav for: Supplier Admin, Sales Manager,
  Territory Manager, Ambassador Manager, Ambassador (shows as "My Events")
- Active state highlighting via 'event' in url_name

---

## Future Considerations / Unscheduled Features

These items are acknowledged and agreed upon but have no assigned phase or timeline.
They are recorded here so they are not forgotten and can inform design decisions
in adjacent phases.

### Account Photos
Account Detail will eventually display photos associated with that account, sourced
from event recaps. Photos are associated to both the event and the account at the
time of recap submission. No timeline or phase assigned.

### Account Detail — Associated Items Display
Account Detail displays associated items grouped by brand, showing item name,
current price (as currency, or "No price recorded" if null), and date first
associated. Items sorted by brand name then item sort_order within each brand.
If no AccountItem records exist, shows empty state message. Section is read-only.
(Built in the Phase 10.3.3 tweaks session.)

---

## Phase 10.3.1 — Event Detail UI Reorganization & Admin Event Flow Fix

> ✅ **Complete and tested.** All features in this phase have been built,
> verified, and pushed to GitHub.

### Event Detail Screen — Final Layout

**Top bar**
- Event Type badge, Status badge, and Edit button are in the top bar to the right
  of the Events back button. Account name does not appear in the top bar.

**Event Details card** (renamed from "Location")
- Date, Start Time, and Duration displayed as **values only** (no labels) at the
  top of the card, before the account name — values separated by spacing
- Start Time is hidden for Admin events
- Account name appears below the date/time block, with address and city displayed
  inline immediately after: `[Account Name], [Address], [City]`
- State and Zip Code are not shown
- Distributor displays inline below the account: `Distributor: [name]`
- Ambassador and Event Manager appear in the same card below the account block,
  with their role title labels (info-label style), displayed side by side

**People card — removed**
- There is no separate People card
- Ambassador and Event Manager are folded into the Event Details card (above)
- Created By is not shown anywhere on the detail page

**Items section**
- Items to be Sampled visible during Draft and Scheduled status
- Hidden once recap workflow is active (Recap In Progress, Recap Submitted,
  Revision Requested, Complete)

### Admin Event Flow Fix
- Releasing an Admin event sends it directly to Recap Submitted (skips Scheduled)
- The Request Revision action is hidden for Admin events
- Move Back to Draft (unrelease) only applies to Scheduled events; not available
  for Admin events since they skip Scheduled

### Event List Screen Updates
- Address and city displayed below the account name on both mobile cards and
  desktop table rows: `[Address], [City]`
- Date format changed to MM/DD/YY throughout (both list and detail)
- Draft events display with a light red background (`#fff5f5`) on both mobile
  cards and desktop table rows — same color as Revision Requested, intentional
  (both require manager action); Revision Requested retains its red left border
  treatment in addition

### Tasting Event Release Validation
- A Tasting event cannot be released unless at least one item is associated
- If attempted with no items, release is blocked and a clear error message is shown
- Festival and Admin events are not subject to this requirement

---

## Phase 10.3.2 — Account-Item Association

> ✅ **Complete and tested.** All features in this phase have been built,
> verified, and pushed to GitHub.

### Purpose
Tracks which productERP Items have been sold at which Accounts, derived
automatically from sales data imports. This is the foundation for the
tasting event recap (shelf price capture per item per account).

### AccountItem Model (apps/accounts/models.py)
- `account`: FK to accounts.Account, CASCADE
- `item`: FK to catalog.Item, CASCADE — always the internal productERP Item record,
  never the raw distributor item code
- `date_first_associated`: DateField — set on creation (the import date), never updated
- `current_price`: DecimalField (max_digits=6, decimal_places=2), null/blank —
  populated only via event recap, never during import
- Unique together: (account, item)
- `__str__`: "{account} — {item}"

### AccountItemPriceHistory Model (apps/accounts/models.py)
- `account_item`: FK to AccountItem, CASCADE, related_name='price_history'
- `price`: DecimalField (max_digits=6, decimal_places=2)
- `recorded_at`: DateTimeField, auto_now_add=True
- `recorded_by`: FK to AUTH_USER_MODEL, SET_NULL, null/blank — null when set by
  the system; populated with the submitting user when captured via recap
- `__str__`: "{account_item} @ {price} on {recorded_at}"
- No price history records are created during import

### Sales Import Update (_execute_import in apps/imports/views.py)
- After bulk account creation (so all Account PKs are available), collects all
  unique (account, item) pairs from the import rows into a `seen_pairs` set
- Calls `AccountItem.objects.get_or_create(account=..., item=...,
  defaults={'date_first_associated': today})` for each unique pair
- If the pair already exists, nothing changes — date_first_associated is never
  overwritten on re-import
- No current_price set during import
- No AccountItemPriceHistory records created during import
- `account_items_created` count stored on ImportBatch and shown in batch detail

### ImportBatch Statistics
- New field `account_items_created` (IntegerField, default=0) added to ImportBatch
- Displayed as "Account-Item Links Created" in the batch detail template

---

## Phase 10.3.3 — Event Recap Form (Tasting + Festival)

> ✅ **Complete and tested.** All features in this phase have been built,
> verified, and pushed to GitHub.

### Status: Recap In Progress
- New status added between Scheduled and Recap Submitted
- Set automatically on first recap save (Scheduled → Recap In Progress)
- Subsequent saves while in Recap In Progress leave status unchanged
- Status only moves to Recap Submitted when ambassador hits the Submit button
- Unlock (Recap Submitted → Recap In Progress) is the reverse of Submit; not
  a full rollback to Scheduled

### Recap Access Rules
- Assigned Ambassador, assigned Event Manager, and any user whose coverage areas
  include the event account can fill out the recap
- Access is determined via get_users_covering_account() (apps/accounts/utils.py)
- Event setup fields remain read-only to these users — only recap fields are editable
- Admin events: no recap form shown

### Recap Form — Placement & Visibility
- Recap form is embedded in the Event Detail screen below the read-only event info
- Form is active (editable) when status is Scheduled, Recap In Progress,
  or Revision Requested
- Form is shown read-only when status is Recap Submitted or Complete
- When status is Revision Requested, the revision note displays prominently at the
  top of the recap form in a highlighted alert box before any input fields
- Previously entered recap data is preserved and editable after a revision request

### Tasting Recap — Part 1: Overall Event
- Number of samples poured (integer input)
- Number of QR codes scanned (integer input)
- General notes (textarea)
- Photo upload: multiple files allowed; count indicator shows how many photos are
  staged ("3 photos selected"); staged client-side, uploaded on form submission;
  no thumbnail preview required; photos associated to both Event and Account on save

### Tasting Recap — Part 2: Per Item
- One section per item associated to the event, labeled with item name
- Fields stacked vertically within each section:
  - Shelf Price (decimal)
  - Bottles Sold (integer)
  - Bottles Used for Samples (integer)
- On submission, update AccountItem.current_price per Phase 10.3.2 rules:
  - If no current price exists, set it
  - If current price exists and new price differs, archive old to AccountItemPriceHistory
    with recorded_by = submitting user, then overwrite current_price
  - If price is unchanged, no history record created

### Festival Recap
- Comment box (textarea)
- Photo upload — same structure as Tasting overall photos (multiple files, count
  indicator, staged client-side, associated to Event and Account on save)
- No per-item section

### New Models

**EventPhoto** (apps/events/models.py)
- event: FK to Event, CASCADE, related_name='photos'
- account: FK to accounts.Account, SET_NULL, null/blank
- file_url: CharField max_length=500
- uploaded_at: DateTimeField, auto_now_add=True
- uploaded_by: FK to AUTH_USER_MODEL, SET_NULL, null/blank
- `__str__`: "Photo for {event} uploaded by {uploaded_by}"

**EventItemRecap** (apps/events/models.py)
- event: FK to Event, CASCADE, related_name='item_recaps'
- item: FK to catalog.Item, CASCADE
- shelf_price: DecimalField max_digits=6 decimal_places=2, null/blank
- bottles_sold: IntegerField, null/blank
- bottles_used_for_samples: IntegerField, null/blank
- Unique together: (event, item)
- `__str__`: "{item} recap for {event}"

### Event Model — New Fields
- recap_samples_poured: IntegerField, null/blank
- recap_qr_codes_scanned: IntegerField, null/blank
- recap_notes: TextField, blank=True
- recap_comment: TextField, blank=True (Festival only — general comment box)

### Photo Storage Abstraction (utils/storage.py or similar)
- Environment-driven via USE_OBJECT_STORAGE env var (True/False)
- False (development): Django FileSystemStorage; files saved to MEDIA_ROOT
- True (production): S3-compatible object storage (Cloudflare R2); stub cleanly
  so actual R2 integration can be added without changing upload logic
- Required env vars: USE_OBJECT_STORAGE, OBJECT_STORAGE_BUCKET_NAME,
  OBJECT_STORAGE_ACCOUNT_ID, OBJECT_STORAGE_ACCESS_KEY_ID,
  OBJECT_STORAGE_SECRET_ACCESS_KEY, OBJECT_STORAGE_PUBLIC_URL
- DEPLOYMENT.md in project root documents all required env vars

### Save / Submit / Unlock Workflow
- **Save**: recap data written; if Scheduled → Recap In Progress; else status unchanged;
  photos uploaded; returns to recap form with success message
- **Submit**: same as Save but status → Recap Submitted; no minimum field requirement —
  submission is allowed with any combination of filled or empty fields; redirects to event detail
- **Unlock**: Recap Submitted → Recap In Progress; available to Ambassador, Event Manager,
  and coverage-area users

### Event List — Action-Required Highlighting
Light red background (#fff5f5) applies to any event requiring user action:
- Draft
- Recap In Progress
- Revision Requested

### New Status Transitions
- POST /events/<id>/save-recap/ — saves recap; Scheduled → Recap In Progress on first save
- POST /events/<id>/submit-recap/ — saves and moves to Recap Submitted
- POST /events/<id>/unlock-recap/ — Recap Submitted → Recap In Progress

---

*Last updated: March 2, 2026*
*Maintained by: Drink Up Life, Inc / productERP project team*
