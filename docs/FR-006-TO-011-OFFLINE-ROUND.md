# FR-006–011 — Offline-round features

**Priority:** P1 | **Status:** designed; implementation follows the online prototype

These six features appear in the supplied hackathon problem statement under the offline round. They use the lesson, narration, quiz and media contracts from FR-001–005. Implementation order can change when the competition gives its final instructions.

## FR-006 — Multi-level content adaptation

**Requirement:** generate primary, secondary and undergraduate explanations for the same lesson.

Keep the factual concept model and evidence refs common. Add a `LevelProfile` with assumed prerequisites, vocabulary, example type, mathematical depth, sentence length and diagram complexity. Build separate `LessonVariantVersion` outputs from the approved source; each variant gets its own scenes, narration and quiz blueprint where needed.

**Why this design:** merely changing the words is insufficient. A primary-school lesson might use a simple moving cart; an undergraduate lesson may include vectors and equations. Both must agree about the underlying physical relation.

**Tests:** three visibly different levels explain the same core relationship; important facts do not contradict one another; every version is labeled by level and source; revising one level does not silently change the others.

**Still to decide:** level-specific curriculum standards, vocabulary controls and the depth checker.

## FR-007 — AI voice generation and multilingual support

**Requirement:** narration and subtitles in selected languages, with consistent visual meaning and synchronization.

Keep concept IDs, formulas and scene intent language-neutral. For each locale, maintain a translated script, a small technical terminology glossary, pronunciation overrides, chosen voice, generated audio and independent cue/caption tracks. The audio-led timing pass runs again for each language: a translation often needs more or less time than the original script.

**Tests:** at least two supported languages with checked terminology, understandable speech, matching captions and aligned visual actions. Changing the locale does not alter the approved scientific content. A failed language render does not damage the source-language publication.

**Still to decide:** which languages/voices are available from the chosen provider, script quality review and right-to-left layout support.

## FR-008 — Accessibility mode

**Requirement:** captions, descriptions, sign-language overlays, high contrast, narration speed and accessible lesson controls.

Start with the FR-003 transcript and timed captions, keyboard-operable controls, visible focus, scalable labels and readable color contrast. Add high-contrast themes and speed control as verified player settings. Record audio descriptions for visual information that is not conveyed in narration. Where sign-language media is required, use a reviewed recording or approved overlay; **do not claim unvalidated automatic sign-language translation**.

**Tests:** full keyboard navigation of player and questions; captions/transcript accessible; no essential information carried by color alone; a tested diagram has an adequate description; sign-video overlay, when provided, stays in sync. Use a relevant accessibility standard and manual user checks before making compliance claims.

**Still to decide:** supported assistive technologies, description authoring process, available sign-language content and exact conformance target.

## FR-009 — Interactive learning experience

**Requirement:** pause at important concepts, ask a question, show hints/explanations and continue the lesson.

A `CheckpointEvent` points to a published lesson version, scene ID, timed cue, question version, hint policy and resume position. The player pauses when the cue is reached. An answer produces a durable attempt event; if the learner seeks backward, the same checkpoint is not accidentally graded twice unless a new permitted attempt begins. Hints and explanation are tied to the verified question key.

**Tests:** seek/pause/resume works at exact scene cue; wrong-answer hint and final explanation work; reload preserves progress; repeated browser events do not duplicate scores; access to teacher-only answer material remains blocked.

**Still to decide:** retry rules, whether a student can skip checkpoints, and the offline handling of checkpoints.

## FR-010 — Offline learning package

**Requirement:** download the lesson, summary and assessment; watch and practice offline; sync progress on reconnect.

Export a `LessonPackageManifest` containing the published media, captions/transcript, summary, practice quiz, checkpoint timeline, package version, hashes and usage permissions. The package should be playable without calling the API. Record local progress in a unique-event journal; after reconnect, validate scope and deduplicate events server-side.

**Limits:** downloaded content cannot be instantly revoked while a device remains offline. Define expiry, DRM/retention expectations and the server's behavior when a package is withdrawn. Offline quiz keys are only suitable for practice, not trustworthy high-stakes grading.

**Tests:** disconnect the network and complete a sample lesson; reconnect and sync once; replay the same journal without creating duplicates; reject corrupted or mismatched package content.

**Still to decide:** package format, device storage limits, download expiry, conflict policy and content rights.

## FR-011 — Teacher editing and customization

**Requirement:** educators can correct scenes, scripts, illustrations, annotations and learning materials before publishing.

Edit the *structured scene/template properties*, not raw renderer code. Let teachers reorder scenes, change approved labels/values, replace a licensed diagram, adjust script wording and add explanatory annotations. Every edit becomes a new artifact version. Changes to the script invalidate audio, captions and affected visual timing; an illustration change may only require a scene rerender. Run validation and publication checks again before release.

**Tests:** replace one diagram safely without changing an unrelated quiz key; correct narration and regenerate the correct timing; reject invalid primitives; trace old/new versions; publication cannot reuse an outdated approval for a revised outline.

**Still to decide:** undo/redo, collaborative editing, preview latency, authoring permissions and support for uploaded asset licenses.

## Practical P1 order

A limited editor is useful first because it lets a teacher fix imperfect generated content. Level adaptation and multilingual tracks follow from the same validated scene and script contracts. Accessibility work is already partly present in P0. Interactive checkpoints and offline export are added after the lesson manifest and quiz events are stable.

Use the actual event instructions to adjust this order. The design does not promise that every offline-round feature is already functioning.
