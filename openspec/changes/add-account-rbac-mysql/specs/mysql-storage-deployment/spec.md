# MySQL Storage and Deployment

## ADDED Requirements

### Requirement: MySQL as canonical persistent storage
The system SHALL store accounts, permissions, authentication sessions, conversation records, projections, guidance, memory, Agent Runs/events and DeerFlow persistent runtime state in MySQL.

#### Scenario: Restart services
- **WHEN** web, worker and MySQL containers restart while their persistent volumes are retained
- **THEN** accounts, roles, conversations, memory and resumable Run state remain available
- **AND** authority does not silently fall back to SQLite or Markdown state files.

#### Scenario: Concurrent updates
- **WHEN** multiple processes update records, event sequences or role assignments
- **THEN** database transactions, unique keys and version checks preserve consistency
- **AND** a process-local Python lock is not the sole concurrency control.

### Requirement: Complete DeerFlow MySQL adaptation
The system MUST provide and validate MySQL adapters for the pinned DeerFlow checkpointer, Store, application persistence and memory paths used by the deployment.

#### Scenario: Resume actual runtime execution
- **WHEN** a worker restarts during a real DeerFlow Run with committed checkpoints or pending writes
- **THEN** a replacement worker restores the compatible state from MySQL
- **AND** a final-answer-only snapshot is not treated as successful checkpoint support.

#### Scenario: A provider is unsupported or unavailable
- **WHEN** required MySQL persistence cannot be configured for the pinned runtime
- **THEN** readiness fails with a specific configuration error
- **AND** deployment does not substitute an unrequested database or ephemeral in-memory store.

### Requirement: Explicit legacy migration and ownership
The system SHALL import legacy SQLite and Markdown data through a versioned, repeatable migration with explicit real-account ownership mapping.

#### Scenario: Import legacy data twice
- **WHEN** an operator reruns an import for the same source objects
- **THEN** stable mapping keys prevent duplicate records and preserve source ordering, visibility and withdrawal state.

#### Scenario: An old Talker has no account mapping
- **WHEN** imported data cannot be safely assigned to a registered account
- **THEN** it remains in an unclaimed legacy scope available only to authorized migration administrators
- **AND** a newly registered user cannot claim it by choosing a matching display name or URL parameter.

#### Scenario: Validate cutover and rollback
- **WHEN** a migration is prepared for deployment
- **THEN** dry-run counts, references, checksums and ownership mapping are reviewed before cutover
- **AND** rollback retains backups and explicitly accounts for writes made after cutover.

### Requirement: Independent MySQL Compose service
The system SHALL manage an independent MySQL container with a persistent database volume, readiness checks and an application-scoped database account through Docker Compose.

#### Scenario: Fresh deployment
- **WHEN** an operator starts an empty deployment
- **THEN** MySQL becomes healthy before migration, administrator bootstrap and application startup complete in order
- **AND** the default database port is not published publicly.

#### Scenario: External model deployment
- **WHEN** the external-model Compose override is selected
- **THEN** Ollama initialization may be omitted
- **AND** MySQL, schema migration, administrator bootstrap and worker readiness remain required.

#### Scenario: Initialization fails
- **WHEN** database migration or administrator bootstrap fails
- **THEN** deployment exits with a nonzero status and identifies the failed phase
- **AND** it does not report the login/chat service as ready.

### Requirement: Safe persistent deployment lifecycle
The system SHALL preserve database and artifact volumes on ordinary shutdown and provide documented backup, restore and explicit reset procedures.

#### Scenario: Stop and start an existing deployment
- **WHEN** an operator executes the ordinary shutdown and startup scripts
- **THEN** existing data and administrator credentials are preserved without re-seeding passwords.

#### Scenario: Restore a backup
- **WHEN** an operator restores the database and matching artifact volume backup
- **THEN** login, RBAC, record ownership and file references can be validated together.

### Requirement: Post-deployment authentication acceptance
The system SHALL validate the deployed registration, password login, administrator grants, authorization and persistence paths in addition to process health.

#### Scenario: Accept a deployment
- **WHEN** post-deployment verification is executed
- **THEN** it covers ordinary registration, both contact-based password logins, default-admin password change, role grant/revoke, cross-user denial and restart persistence
- **AND** a successful health endpoint alone is insufficient to mark the deployment accepted.
