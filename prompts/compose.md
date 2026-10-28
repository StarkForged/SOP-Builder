Create one SOP from the sources below.

Title hint (data, may be empty): {title_hint}

Requirements:
- title, purpose (one or two sentences, from the sources).
- prerequisites (ids pre-1, pre-2, ...): access, tools, documents needed before starting.
- steps (ids step-1, step-2, ...; number 1..n in order). kind: "action", "decision" (fill condition with the exact criteria from the sources) or "verification". actor = the role doing it if stated, else null. ui_elements = exact on-screen labels quoted in the sources only. expected_result if stated, else null.
- warnings (ids warn-1, ...): severity "caution" or "critical".
- questions (ids q-1, ...): what staff must confirm. category from the schema enum; severity "mandatory" or "optional"; related_item_ids; origin "llm".
- checklist: may be empty (it is rebuilt by the system).
- Cite source_refs for every item using segment ids from <sources> and quotes copied verbatim.
