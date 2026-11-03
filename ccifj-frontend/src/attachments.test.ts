import { describe, expect, it } from 'vitest';
import { ACCEPTED_FILES, attachmentType, toAttachmentUploads } from './attachments';

describe('attachment uploads', () => {
  it('maps files to the types the server reads, including untyped Markdown', () => {
    expect(attachmentType(new File(['# hi'], 'notes.md'))).toBe('text/markdown');
    expect(attachmentType(new File([''], 'Scan.JPG', { type: 'image/jpeg' }))).toBe('image/jpeg');
    expect(attachmentType(new File([''], 'clip.gif', { type: 'image/gif' }))).toBeUndefined();
    expect(ACCEPTED_FILES).toContain('.docx');
  });

  it('sends the file content as base64', async () => {
    const [upload] = await toAttachmentUploads([new File(['hello'], 'note.txt')]);
    expect(upload).toEqual({ name: 'note.txt', mime_type: 'text/plain', data_base64: 'aGVsbG8=' });
  });
});
