# Personal Employee Runtime

## ADDED Requirements

### Requirement: Real DeerFlow as the execution core
The system SHALL execute every newly accepted interactive Agent Run through a pinned official DeerFlow 2.x harness and its lead-agent runtime.

#### Scenario: Execute a tool-backed request
- **WHEN** an authenticated user submits a request requiring an enabled tool
- **THEN** the Run invokes the actual DeerFlow lead agent and tool runtime
- **AND** the returned result and tool events reference the same persisted Run.

#### Scenario: Runtime initialization fails
- **WHEN** the pinned DeerFlow runtime or required persistence provider cannot initialize
- **THEN** readiness fails and new Runs are not reported as accepted
- **AND** the system does not silently execute the retired custom question-answering agent.

### Requirement: General employee and meeting profiles
The system SHALL provide a general personal-employee prompt and a meeting-assistant work profile without requiring every user input to be a question.

#### Scenario: Continuous statements and a request
- **WHEN** a user states meeting facts and then requests an explanation or work result
- **THEN** the system preserves the statements and uses the relevant conversation context
- **AND** the Agent responds or performs an authorized task without requiring question-format rewriting.

#### Scenario: A requested metric is missing
- **WHEN** a request is understood but authorized sources do not contain its answer
- **THEN** the Agent explains the actual evidence gap or requests the specific missing information
- **AND** it does not treat missing factual knowledge as an instruction to repeatedly re-transcribe clear speech.

#### Scenario: Chinese meeting input
- **WHEN** a user records a Chinese meeting and has not requested English output
- **THEN** the original records preserve their language and the Agent follows the user's conversation language or explicit language setting.

### Requirement: Principal-scoped tools and memory
The system MUST derive the acting principal, workspace owner and permitted tools from authenticated server context for the lead agent and every delegated task.

#### Scenario: Recall personal memory
- **WHEN** a user opens a second conversation and asks about an authorized fact stored in their memory
- **THEN** the DeerFlow memory path retrieves that user's persisted fact
- **AND** the answer preserves its source, scope and uncertainty.

#### Scenario: Another user's resource is requested
- **WHEN** an ordinary user or a delegated tool attempts to read another user's record, checkpoint, memory or artifact
- **THEN** the server denies access before returning the resource to the model or caller.

#### Scenario: Hinter contributes private context
- **WHEN** an authorized Hinter adds a private note for a selected user's conversation
- **THEN** its actor, owner and scope remain distinct
- **AND** unauthorized users, public projections and unrelated tool calls cannot receive its content.

### Requirement: Durable generic Run lifecycle
The system SHALL persist accepted Runs and their execution checkpoints, retry state and terminal outcomes in the MySQL-backed runtime storage defined by the companion change.

#### Scenario: A new message arrives during work
- **WHEN** an earlier accepted Run is still executing and the user sends another message
- **THEN** the new input is recorded and queued according to the thread policy
- **AND** the earlier Run is not silently deleted or replaced by the new message.

#### Scenario: Process failure and recovery
- **WHEN** a worker exits during an uncancelled Run
- **THEN** a replacement worker can recover the Run from persisted state and a valid lease
- **AND** completed external side effects are not blindly repeated.

#### Scenario: Stop and playback interruption
- **WHEN** the user stops audio playback or explicitly cancels a Run
- **THEN** the system distinguishes these two control events
- **AND** a cancelled Run stays cancelled across restart.

### Requirement: Honest task results
The system SHALL expose meaningful execution outcomes and SHALL NOT report a tool action as completed without a successful authorized result.

#### Scenario: External lookup fails
- **WHEN** configured sources fail or return no supporting evidence within the Run budget
- **THEN** the Run records the actual failure or evidence limitation and provides a bounded user-facing explanation
- **AND** no retrieved fact or completed action is fabricated.
