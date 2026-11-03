import type { AttachmentUpload } from './types';

/** Matches the server: four distinct files per conversation, 5 MB each. */
export const MAX_FILES_PER_CONVERSATION = 4;
export const MAX_FILE_BYTES = 5 * 1024 * 1024;

const TYPES_BY_EXTENSION: Record<string, string> = {
  pdf: 'application/pdf',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  txt: 'text/plain',
  md: 'text/markdown',
  csv: 'text/csv',
  json: 'application/json',
  xml: 'application/xml',
  html: 'text/html',
  htm: 'text/html',
};

/** The file picker's `accept` list: only types the server can read. */
export const ACCEPTED_FILES = Object.keys(TYPES_BY_EXTENSION).map(extension => `.${extension}`).join(',');

/** The server's MIME type for a file; browsers often leave Markdown or CSV untyped. */
export function attachmentType(file: File): string | undefined {
  const extension = file.name.toLowerCase().split('.').pop() ?? '';
  return TYPES_BY_EXTENSION[extension];
}

async function readBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let binary = '';
  // Chunked so large files don't exceed the argument limit of String.fromCharCode.
  for (let index = 0; index < bytes.length; index += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(index, index + 0x8000));
  }
  return btoa(binary);
}

/** Encode files for the chat request; text extraction and OCR happen on the server. */
export async function toAttachmentUploads(files: File[]): Promise<AttachmentUpload[]> {
  return Promise.all(files.map(async file => ({
    name: file.name.slice(0, 200),
    mime_type: attachmentType(file) ?? file.type,
    data_base64: await readBase64(file),
  })));
}
