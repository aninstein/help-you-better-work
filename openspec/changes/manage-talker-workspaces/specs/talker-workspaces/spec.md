# Talker Workspaces

## ADDED Requirements

### Requirement: Persistent Talker directory
The system SHALL persist Talkers and expose a homepage create action, Talker list, and a single Hinter entry.

#### Scenario: Create and revisit
- **WHEN** a user creates a named Talker
- **THEN** the Talker and its initial Session appear in the directory and survive restart
- **AND** opening that Talker enters its own space.

### Requirement: Talker-owned Wiki
The system SHALL bind Wiki and private Hint constraints to the selected Talker, shared across that Talker's Sessions.

#### Scenario: Switch owners and sessions
- **WHEN** Hinter switches between Sessions of one Talker
- **THEN** session data changes and the Wiki owner remains the same.
- **WHEN** Hinter selects another Talker
- **THEN** the loaded Wiki and constraints belong to that Talker alone.

#### Scenario: Preserve legacy data
- **WHEN** the upgraded application starts
- **THEN** historical Sessions and Wiki remain available under the default Talker without duplicating them.

### Requirement: Unified Hinter workspace selection
The Hinter SHALL select a Talker and Session through a directory, loading messages, questions, guidance, call supervision, and Wiki for that selection.

#### Scenario: In-flight selection changes
- **WHEN** Hinter changes selection while an earlier request or realtime event is pending
- **THEN** the old result cannot replace the selected workspace or its forms.

#### Scenario: Observe history during a live call
- **WHEN** Hinter selects another Session or Talker
- **THEN** the observed call is not hung up and a reset elsewhere does not move Hinter away from the chosen Session.

#### Scenario: Loading fails
- **WHEN** the selected Session cannot load
- **THEN** an error and retry action appear while the directory remains usable.
