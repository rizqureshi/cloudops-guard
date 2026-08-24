/**
 * Reads a request body under a fixed byte ceiling (Phase 3I), enforced
 * twice: a declared `Content-Length` above the limit is rejected before any
 * read occurs, and the body is then read incrementally, stopping the
 * instant actual bytes exceed the limit -- covering a chunked or absent-
 * `Content-Length` request, and an honest-looking but dishonest one alike.
 * `request.text()`/`request.json()` (an unbounded whole-body read) are
 * never called anywhere in this Worker.
 */

export const MAX_CONTACT_BODY_BYTES = 8 * 1024;

export type BodyReadResult =
  | { readonly kind: "ok"; readonly bytes: Uint8Array }
  | { readonly kind: "too_large" }
  | { readonly kind: "invalid" };

const DIGITS_ONLY_PATTERN = /^\d+$/;

export async function readBoundedBody(request: Request, maxBytes: number): Promise<BodyReadResult> {
  const contentLengthHeader = request.headers.get("content-length");
  if (contentLengthHeader !== null) {
    if (!DIGITS_ONLY_PATTERN.test(contentLengthHeader)) {
      return { kind: "invalid" };
    }
    if (Number(contentLengthHeader) > maxBytes) {
      return { kind: "too_large" };
    }
  }

  if (!request.body) {
    return { kind: "ok", bytes: new Uint8Array(0) };
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      if (value && value.byteLength > 0) {
        total += value.byteLength;
        if (total > maxBytes) {
          await reader.cancel();
          return { kind: "too_large" };
        }
        chunks.push(value);
      }
    }
  } catch {
    return { kind: "invalid" };
  }

  const combined = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { kind: "ok", bytes: combined };
}
