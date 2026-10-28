Create one SOP from the sources below. Title hint (data, may be empty): {title_hint}

Return ONE JSON object with exactly these keys (no other keys, no markdown):
- title: string; purpose: 1-2 sentences from the sources
- prerequisites: [{id:"pre-N", text, source_refs, status}] (access, tools, documents)
- steps (at least 1, in order): [{id:"step-N", number:N (1..n), kind:"action"|"decision"|"verification", actor:string|null, action:string, condition:string|null (exact decision criteria from the sources), expected_result:string|null, ui_elements:[exact on-screen labels from the sources], source_refs, status}]
- warnings: [{id:"warn-N", text, severity:"caution"|"critical", source_refs, status}]
- questions: [{id:"q-N", question, reason, category, severity:"mandatory"|"optional", related_item_ids:[item ids], origin:"llm"}]
- checklist: [] (the system builds it)
source_refs = [{segment_id:"S1-0002", quote: 3-300 chars copied verbatim from that segment}]
status = "supported"|"inferred"|"needs_confirmation"
category = {categories}

Shape example (not content): {"title":"T","purpose":"P","prerequisites":[],"steps":[{"id":"step-1","number":1,"kind":"action","actor":null,"action":"Open the X page.","condition":null,"expected_result":null,"ui_elements":["X"],"source_refs":[{"segment_id":"S1-0001","quote":"open the X page"}],"status":"supported"}],"warnings":[],"questions":[],"checklist":[]}
