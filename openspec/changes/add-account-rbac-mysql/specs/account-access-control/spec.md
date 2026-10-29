# Account Access Control

## ADDED Requirements

### Requirement: Unified account registration
The system SHALL register ordinary accounts using a unique normalized phone number, a unique normalized email address and a user-chosen password, with an optional display name.

#### Scenario: Register and enter a personal workspace
- **WHEN** a visitor submits valid registration fields
- **THEN** the system creates a durable account with the Talker role and a personal Agent workspace
- **AND** the account can subsequently log in using either its phone number or email with the password.

#### Scenario: Duplicate or privileged registration
- **WHEN** concurrent registrations reuse a phone/email or a registration request attempts to choose an administrator role
- **THEN** database uniqueness and server validation reject the invalid operation
- **AND** public registration cannot create Hinter privileges.

#### Scenario: Contact verification is not configured
- **WHEN** an account registers in the password-only first release
- **THEN** its phone/email verification state remains explicit and unverified
- **AND** the system does not send or require an unimplemented OTP flow.

### Requirement: Password authentication and server sessions
The system MUST verify password hashes and issue revocable server-managed sessions without accepting client-selected roles as identity.

#### Scenario: Valid and invalid password login
- **WHEN** a visitor logs in using a registered phone/email and password
- **THEN** valid credentials establish a protected session and return current server-derived roles
- **AND** invalid credentials return a uniform failure without exposing password data.

#### Scenario: Login abuse
- **WHEN** repeated failed login attempts exceed the configured account/source limit
- **THEN** the system rate-limits further attempts without exposing whether the account exists.

#### Scenario: Logout or password change
- **WHEN** a user logs out or changes their password
- **THEN** the corresponding old session becomes unusable for protected HTTP, stream and file operations
- **AND** no raw password or session token is returned by a user-management API.

### Requirement: Deployment-specific default administrator
The system SHALL initialize a default account displayed as Hinter Admin using a unique cryptographically random password of at least 24 characters and SHALL require a first-login password change.

#### Scenario: First installation
- **WHEN** the database is migrated and no bootstrap administrator exists
- **THEN** initialization creates the administrator, stores only the password hash and writes the initial credential to a restricted local file
- **AND** the login cannot perform management operations until the first password change completes.

#### Scenario: Restart an installed deployment
- **WHEN** initialization runs again against an existing deployment
- **THEN** it preserves existing passwords and explicit role changes
- **AND** it does not print a new universal default password or silently restore revoked privileges.

### Requirement: Hinter RBAC administration
The system SHALL allow an authorized Hinter administrator to grant and revoke the Hinter administrator role for registered Talker accounts and SHALL audit each change.

#### Scenario: Promote a registered user
- **WHEN** an administrator grants `hinter_admin` to an existing Talker
- **THEN** subsequent authenticated capability checks expose the Hinter workspace and user-management permissions
- **AND** the account retains its own personal Agent workspace.

#### Scenario: Revoke access during an active subscription
- **WHEN** Hinter access is revoked while the account has an active protected stream or Agent task
- **THEN** subsequent protected operations and data delivery enforce the updated role version
- **AND** stale connections or delegated tasks cannot continue exercising the revoked administrator scope.

#### Scenario: Concurrent removal of the final administrator
- **WHEN** role or account-status changes would leave no active administrator
- **THEN** a transaction rejects the operation even under concurrent requests.

### Requirement: Owner-scoped resource authorization
The system MUST authorize conversation, record, memory, Run, audio and artifact access using the authenticated actor and actual resource owner.

#### Scenario: Ordinary user modifies an identifier
- **WHEN** a Talker substitutes another user's resource identifier or adds `role=hinter` to a URL
- **THEN** the system denies unauthorized access before returning any resource content.

#### Scenario: Administrator supervises a user
- **WHEN** a Hinter administrator selects another user's workspace
- **THEN** the selected owner and actual administrator actor remain distinct in records, tools and audit
- **AND** private data is not mixed into the administrator's own or another user's memory.

### Requirement: Login-aware chat and administration UI
The system SHALL require login before chat, show current identity in the upper-right area and derive management navigation from server capabilities.

#### Scenario: Visit without a valid session
- **WHEN** a visitor opens a protected chat or management URL
- **THEN** a login page appears with a registration option
- **AND** successful login returns to an authorized destination or the personal Agent chat.

#### Scenario: Session expires while composing
- **WHEN** a session expires while a user has an unsent draft
- **THEN** the UI requests login and preserves the in-page draft
- **AND** already submitted inputs are not automatically duplicated after login.

#### Scenario: Logged-in user sees identity and Agent information
- **WHEN** an ordinary user or administrator opens the chat page
- **THEN** the page displays account status, roles, logout and the selected personal Agent information
- **AND** management entry points appear only when the account has the required capabilities.
