# Contextual Human Assistance

## ADDED Requirements

### Requirement: Private assistance brief
The system SHALL attach a private assistance request to a question requiring human information, describing the question and the specific help needed without inventing an answer.

#### Scenario: Ask for a metric
- **WHEN** a caller asks for TikTok DAU and human data is needed
- **THEN** Hinter sees a request for TikTok's DAU value tied to the original question
- **AND** the task does not invent a value or become another question to the caller.

### Requirement: Compose an answer from contextual human input
The system SHALL combine original caller input, the assistance request and the human input to generate a concise English answer, preserving supplied values and restrictions.

#### Scenario: Supply a short value
- **WHEN** Hinter supplies `200` for the TikTok DAU question
- **THEN** the answer expresses TikTok's DAU as 200 without adding million or an unstated reporting date.

#### Scenario: Supply a percentage
- **WHEN** Hinter supplies `50%` for the AICO efficiency improvement question
- **THEN** the answer states that AICO improves efficiency by 50%.

#### Scenario: Supply a prohibition
- **WHEN** a question-bound Hint says that this information must not be disclosed
- **THEN** the instruction is interpreted against its bound question, remains a prohibition and is not treated as an authorized fact.

### Requirement: Preserve question and owner scope
The system SHALL retain the question association in private Wiki projections and answer-generation context within the selected Talker workspace.

#### Scenario: Multiple pending questions
- **WHEN** an unbound numeric Hint has multiple candidate questions
- **THEN** no question is assigned that value until Hinter chooses its target
- **AND** one standalone value cannot be broadcast to a batch of selected questions.

#### Scenario: Pending questions change during editing
- **WHEN** the original question is resolved while Hinter is still editing a short value
- **THEN** that value remains associated with the original question and cannot silently move to another pending question.

#### Scenario: Revise or revoke
- **WHEN** a question-bound Hint is revised or revoked
- **THEN** its binding remains stable and only applicable active inputs reach subsequent generation.

### Requirement: Preserve automatic response latency
The system SHALL generate the brief within existing planning and use the existing response pipeline without an extra Agent or Wiki-retrieval round trip.

#### Scenario: Ordinary question
- **WHEN** a question needs no human input
- **THEN** it continues to answer automatically without waiting for an assistance brief or review.
