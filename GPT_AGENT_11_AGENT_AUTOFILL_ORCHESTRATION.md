# AutoStop CRM Agent Autofill Orchestration

Current card autofill is not a free-form browser agent. It is a small deterministic orchestration pipeline.

## Pipeline
1. `card analyzer`
   Reads `get_card_context(card_id)`.
   Extracts VIN, complaint, mileage, DTC, likely parts, waiting state, known vehicle facts, missing vehicle fields.
2. `scenario selector`
   Decides which scenarios are relevant:
   - VIN decode
   - part lookup
   - maintenance estimate
   - DTC decode
   - symptom fault search
3. `tool router`
   Calls only the bounded external tools needed for the detected scenario.
4. `result normalizer`
   Converts raw external results into short Russian operational notes.
5. `card writer`
   Applies safe `update_card()` changes:
   - keeps original text
   - appends only net-new `ИИ:` notes
   - patches `vehicle_profile` only where fields are missing
6. `follow-up controller`
   Schedules the next check, skips unchanged cards, stops after the 4-hour window or the run limit.

## External Tools
- `decode_vin(vin)`
- `find_part_numbers(query, vehicle)`
- `estimate_price_ru(part_number, vehicle)`
- `decode_dtc(code, vehicle_context)`
- `search_fault_info(query, vehicle_context)`

## Tool Limits
- External request budget is reset per task.
- Budget is capped to a small number of calls per run.
- Only whitelisted domains and trusted sources are used.
- Results are normalized to compact structured payloads before card updates.

## Writer Rules
- Never delete manual prices, article numbers, phone numbers, VIN, or operator notes.
- Do not duplicate existing card text.
- Add only net-new short lines.
- Prefix AI additions with `ИИ:`.
- If certainty is low, write a short follow-up note for the next executor instead of fabricating facts.

## Scenario Rules

### VIN
- Trigger when VIN exists and some vehicle fields are missing.
- Also trigger when the task text or mini-prompt explicitly asks to decode or confirm VIN.
- Fill only missing `vehicle_profile` fields.

### Parts
- Trigger when the description implies a concrete part such as radiator, control arm, strut, bearing, pads, thermostat, pump, belt, chain, filters, plugs, battery.
- Add OEM or catalog numbers if found.
- Add Russian price orientation if a good part number was found.

### ТО
- Trigger only on real maintenance cues, not on accidental `то` substrings.
- Build a compact preliminary list of works and consumables.

### DTC
- Decode the first detected DTC first.
- Add short meaning and first-check hints.

### Symptoms
- Use only when the card is not obviously in a waiting state.
- Add short diagnostic context, not a wall of text.

## Follow-Up Rules
- First pass runs immediately after enabling autofill.
- Later checks depend on change detection:
  - changed card -> faster revisit
  - unchanged card -> slower revisit
  - waiting state -> slower revisit
- Duplicate active processing for the same card is blocked.
- Old AI runs do not endlessly retrigger themselves.

## Operator Prompt Rules
- Mini-prompt influences scenario selection.
- It should be short and operational:
  - `Расшифруй VIN`
  - `Помоги с подбором радиатора`
  - `Собери ТО по пробегу`
- Prompt is guidance, not permission to overwrite manual facts.
