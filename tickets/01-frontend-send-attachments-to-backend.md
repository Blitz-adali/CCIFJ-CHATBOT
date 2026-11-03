# 01 · Uploaded file text is shown in the chat bubble instead of going to the agent's context

**Area:** Frontend (+ small backend change) · **Priority:** High · **Related task:** 1

## Problem
When a user attaches a file, all of its extracted text appears in the chat next to their prompt. It is also saved as the user's message and sent to the agent as part of the prompt, not as attachment context.

## Cause
`ccifj-frontend/src/WidgetChat.tsx:95` (`ask()`) runs OCR/PDF parsing **in the browser** (`attachments.ts → extractAttachmentText`) and joins the result onto the message:

```ts
message = [message, extracted].filter(Boolean).join('\n\n').slice(0, 4000);
```

The backend already supports attachments properly, but the frontend never uses it:
- `ChatRequest.attachments: list[AttachmentUpload]` (`src/concierge/api/schemas.py:17-34`): `{name, mime_type, data_base64}`, at most 4 files, 5 MB each.
- `chat.py → extract_uploads → attachments.extract_attachment`: pypdf text, plus Tesseract OCR (eng+fra+jpn) for scans and images.
- The extracted text is saved as `attachment_context` metadata and passed to the agent as `context` on every later turn of the conversation (`agents/body.py → _attachment_context_prompt`).

## Fix
### Frontend
1. Add `attachments?: {name: string; mime_type: string; data_base64: string}[]` to `ChatRequest` in `types.ts`.
2. In `ask()`, stop calling `extractAttachmentText`. Read each file as base64 (`FileReader.readAsDataURL`, then strip the `data:...;base64,` prefix) and send it in `attachments`. Keep `message` as exactly what the user typed.
3. In the user bubble, show the file names as chips (paperclip and name), not the text.
4. Keep the existing "Reading your files…" progress state while the request is uploading.
5. Match the backend limits in the UI: at most 4 files per conversation, 5 MB per file. The current UI allows 5 files and 15 MB.
6. `attachments.ts` (browser OCR through the jsdelivr CDN) can then be deleted.

### Backend (small)
`ALLOWED_TYPES` in `src/concierge/api/attachments.py` accepts only PDF, PNG and JPEG, but the file picker offers DOCX, TXT, MD, CSV, JSON, XML and HTML. Either:
- add the text types (decode UTF-8 and truncate to `MAX_TEXT_CHARS_PER_FILE`) and DOCX (`python-docx`), **or**
- limit the picker's `accept` to `application/pdf,image/png,image/jpeg`.

Recommended: add the text types and DOCX.

## Acceptance
- After uploading a PDF or image, the user bubble shows only the typed prompt and the file chip.
- The agent can answer questions about the file's content, in that turn and in later turns.
- The saved user message in the DB contains no extracted text.
- If the server has no Tesseract, the user sees the 503 message ("OCR is unavailable…").
