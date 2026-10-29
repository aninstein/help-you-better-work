# Meeting Conversation Records

## ADDED Requirements

### Requirement: Canonical conversation records
The system SHALL preserve user input, confirmed transcription, Agent output and authorized control records as versioned conversation records independent of question extraction.

#### Scenario: A statement contains no question
- **WHEN** a user says that a pilot ends on Friday without asking a question
- **THEN** the statement remains available in the conversation and as authorized Agent context
- **AND** an empty question projection does not discard the statement.

#### Scenario: Transcription is corrected
- **WHEN** a confirmed transcription is corrected
- **THEN** the system preserves its revision relationship and audio reference
- **AND** projections based on the earlier version are marked stale and refreshed without changing unrelated records.

### Requirement: Question view as a presentation projection
The system SHALL support `presentation_mode=questions` as the initial view over canonical records, without making this view a prerequisite for an Agent Run.

#### Scenario: Extract questions from a meeting turn
- **WHEN** a recorded meeting turn contains facts and multiple explicit questions
- **THEN** the question projection references the originating records and revisions
- **AND** the Agent's execution and output remain linked to the original input.

#### Scenario: Projection processing fails
- **WHEN** question extraction fails after an input was accepted
- **THEN** the original record and Agent Run remain accessible
- **AND** the UI reports a projection failure without falsely reporting the Agent Run as lost.

#### Scenario: Request a future summary view
- **WHEN** a client requests a presentation mode that is reserved but not implemented
- **THEN** the system returns a specific unsupported-capability response
- **AND** existing question records are not relabelled as a completed summary.

### Requirement: Streaming records and reconnect
The system SHALL associate every visible text or audio output with its source Run and SHALL support reconnect using persisted event sequence numbers.

#### Scenario: Connection drops while a task continues
- **WHEN** a client disconnects and reconnects after events were emitted
- **THEN** it can reload authorized records and replay subsequent events without duplicate messages
- **AND** later Run output is not attached to a different user's question or conversation.

#### Scenario: Speech interrupts playback
- **WHEN** new speech interrupts an audio response
- **THEN** the system stops obsolete playback and retains already committed text
- **AND** the input record and Run lifecycle follow the explicit control policy.

### Requirement: Legacy records remain readable
The system SHALL preserve mapped legacy messages, guidance, question projections and withdrawal visibility during migration while routing new inputs through the new runtime.

#### Scenario: Read an imported conversation
- **WHEN** an authorized owner opens a migrated conversation
- **THEN** the original ordering and resource ownership remain visible
- **AND** revoked guidance and withdrawn answer content are not restored as active memory or new public output.
