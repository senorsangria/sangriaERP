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
- Distributor relationships exist at the Brand level, not Company level
- Many-to-many relationship between Distributors and Brands
- A Distributor can service multiple Brands
- A Brand can have multiple Distributors
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
- When a VIP import contains an unrecognized item name, the import
  completes and unrecognized items are queued for admin cleanup
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
- Import completes even if unrecognized items are present
- Unrecognized items are queued in ItemMapping for admin cleanup
- Import does not pause or fail on unmapped items

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
| Phase 1 | Login, User Accounts, Roles | 🔄 In Progress |
| Phase 2 | Distributors, Import Accounts, Import Sales Data (VIP) | ⬜ Pending |
| Phase 3 | Sales Views | ⬜ Pending |
| Phase 4 | Saving Sales Views | ⬜ Pending |
| Phase 5 | CRM — Accounts (contacts, notes) | ⬜ Pending |
| Phase 6 | Sales Reports / Distributor Reports | ⬜ Pending |
| Phase 7 | Sales Orders | ⬜ Pending |
| Phase 8 | Production Ordering | ⬜ Pending |
| Phase 9 | Projection Planning | ⬜ Pending |
| Phase 10 | Tasting / Event Management | ⬜ Pending |

---

## Deferred Features (Not In Current Scope)
- Dynamic account assignment (new imports auto-inherit user assignments)
- Cross-tenant distributor sharing
- Multi-company user accounts (users spanning multiple tenants)
- Tasting Agency as separate tenant/company
- Master Account matching logic (golden record deduplication)
- Profile photos on user accounts
- Mobile app (native) — current approach is responsive web

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

*Last updated: February 2026*
*Maintained by: Drink Up Life, Inc / productERP project team*
