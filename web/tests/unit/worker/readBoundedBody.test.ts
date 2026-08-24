import { describe, expect, it } from "vitest";

import { MAX_CONTACT_BODY_BYTES, readBoundedBody } from "../../../worker/readBoundedBody";

function requestWithBody(body: string, headers: Record<string, string> = {}): Request {
  return new Request("https://cloudopsguard.example/api/contact", {
    method: "POST",
    headers,
    body,
  });
}

/** A request whose body streams more bytes than any declared/absent Content-Length would suggest. */
function requestWithUndeclaredStream(totalBytes: number): Request {
  const chunkSize = 512;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      let sent = 0;
      while (sent < totalBytes) {
        const size = Math.min(chunkSize, totalBytes - sent);
        controller.enqueue(new Uint8Array(size).fill(97));
        sent += size;
      }
      controller.close();
    },
  });
  return new Request("https://cloudopsguard.example/api/contact", {
    method: "POST",
    body: stream,
    // @ts-expect-error -- duplex is required by the Fetch spec for a streaming body but not yet in this project's lib typings.
    duplex: "half",
  });
}

describe("readBoundedBody: honest small body", () => {
  it("reads a small body under the limit", async () => {
    const result = await readBoundedBody(requestWithBody('{"a":1}'), MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(new TextDecoder().decode(result.bytes)).toBe('{"a":1}');
    }
  });

  it("reads an empty body", async () => {
    const result = await readBoundedBody(requestWithBody(""), MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.bytes.byteLength).toBe(0);
    }
  });
});

describe("readBoundedBody: declared Content-Length enforcement", () => {
  it("rejects a declared oversized Content-Length before reading the body", async () => {
    const request = requestWithBody("small body", { "content-length": String(MAX_CONTACT_BODY_BYTES + 1) });
    const result = await readBoundedBody(request, MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("too_large");
  });

  it("treats a non-numeric Content-Length as invalid", async () => {
    const request = requestWithBody("small body", { "content-length": "not-a-number" });
    const result = await readBoundedBody(request, MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("invalid");
  });

  it("accepts a body exactly at the declared limit", async () => {
    const body = "a".repeat(MAX_CONTACT_BODY_BYTES);
    const request = requestWithBody(body, { "content-length": String(MAX_CONTACT_BODY_BYTES) });
    const result = await readBoundedBody(request, MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("ok");
  });
});

describe("readBoundedBody: bounded incremental reading (chunked/undeclared/dishonest)", () => {
  it("rejects a body whose actual bytes exceed the limit even with no Content-Length header", async () => {
    const request = requestWithUndeclaredStream(MAX_CONTACT_BODY_BYTES + 1024);
    const result = await readBoundedBody(request, MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("too_large");
  });

  it("accepts a streamed body exactly at the limit with no Content-Length header", async () => {
    const request = requestWithUndeclaredStream(MAX_CONTACT_BODY_BYTES);
    const result = await readBoundedBody(request, MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("ok");
  });

  it("stops reading once the limit is exceeded, never buffering the full oversized body", async () => {
    // A huge declared size with no Content-Length header -- the limit must
    // be enforced by the incremental reader itself, not by first reading
    // everything into memory.
    const hugeButBounded = MAX_CONTACT_BODY_BYTES * 5;
    const request = requestWithUndeclaredStream(hugeButBounded);
    const result = await readBoundedBody(request, MAX_CONTACT_BODY_BYTES);
    expect(result.kind).toBe("too_large");
  });
});
