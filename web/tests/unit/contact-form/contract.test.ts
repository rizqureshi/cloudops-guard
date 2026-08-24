import { describe, expect, it } from "vitest";

import {
  CONTACT_COMPANY_MAX_LENGTH,
  CONTACT_EMAIL_MAX_LENGTH,
  CONTACT_MESSAGE_MAX_LENGTH,
  CONTACT_NAME_MAX_LENGTH,
  CONTACT_TURNSTILE_TOKEN_MAX_LENGTH,
  contactFormSchema,
} from "../../../src/features/contact-form/contract";

function validInput(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    formType: "pilot_request",
    name: "Ada Lovelace",
    workEmail: "ada@example.com",
    company: "Analytical Engines Ltd",
    message: "We'd like to run a pilot audit.",
    consent: true,
    turnstileToken: "a-real-looking-token",
    ...overrides,
  };
}

describe("contactFormSchema: both form types", () => {
  it("accepts formType 'pilot_request'", () => {
    expect(contactFormSchema.safeParse(validInput({ formType: "pilot_request" })).success).toBe(true);
  });

  it("accepts formType 'feedback'", () => {
    expect(contactFormSchema.safeParse(validInput({ formType: "feedback" })).success).toBe(true);
  });

  it("rejects any other formType value", () => {
    expect(contactFormSchema.safeParse(validInput({ formType: "support" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ formType: "" })).success).toBe(false);
  });
});

describe("contactFormSchema: required fields", () => {
  it("rejects a missing name", () => {
    const { name: _name, ...rest } = validInput();
    expect(contactFormSchema.safeParse(rest).success).toBe(false);
  });

  it("rejects an empty name", () => {
    expect(contactFormSchema.safeParse(validInput({ name: "" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ name: "   " })).success).toBe(false);
  });

  it("rejects a missing workEmail", () => {
    const { workEmail: _workEmail, ...rest } = validInput();
    expect(contactFormSchema.safeParse(rest).success).toBe(false);
  });

  it("rejects a missing message", () => {
    const { message: _message, ...rest } = validInput();
    expect(contactFormSchema.safeParse(rest).success).toBe(false);
  });

  it("rejects an empty message", () => {
    expect(contactFormSchema.safeParse(validInput({ message: "" })).success).toBe(false);
  });

  it("regression: rejects a whitespace-only message (spaces, tabs, and line breaks with no meaningful content)", () => {
    expect(contactFormSchema.safeParse(validInput({ message: "   " })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ message: "\t\t" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ message: "\n\n\n" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ message: " \t\n \r\n " })).success).toBe(false);
  });

  it("accepts a multiline message with meaningful content, preserving its original whitespace exactly", () => {
    const message = "  Line one has real content.\n\nLine two also does.  ";
    const result = contactFormSchema.safeParse(validInput({ message }));
    expect(result.success).toBe(true);
    if (result.success) {
      // Never trimmed or rewritten -- stored exactly as submitted.
      expect(result.data.message).toBe(message);
    }
  });

  it("rejects a missing turnstileToken", () => {
    const { turnstileToken: _turnstileToken, ...rest } = validInput();
    expect(contactFormSchema.safeParse(rest).success).toBe(false);
  });

  it("rejects an empty turnstileToken", () => {
    expect(contactFormSchema.safeParse(validInput({ turnstileToken: "" })).success).toBe(false);
  });
});

describe("contactFormSchema: optional company", () => {
  it("accepts an omitted company", () => {
    const { company: _company, ...rest } = validInput();
    expect(contactFormSchema.safeParse(rest).success).toBe(true);
  });

  it("accepts an empty-string company", () => {
    expect(contactFormSchema.safeParse(validInput({ company: "" })).success).toBe(true);
  });

  it("trims a company value", () => {
    const result = contactFormSchema.safeParse(validInput({ company: "  Acme  " }));
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.company).toBe("Acme");
    }
  });
});

describe("contactFormSchema: exact allowlist (strict object)", () => {
  it("rejects an unknown top-level field", () => {
    expect(contactFormSchema.safeParse(validInput({ extraField: "anything" })).success).toBe(false);
  });

  it("rejects report-shaped or credential-shaped fields", () => {
    expect(contactFormSchema.safeParse(validInput({ reportJson: "{}" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ kubeconfig: "apiVersion: v1" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ gitlabToken: "glpat-xxx" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ attachment: "data:..." })).success).toBe(false);
  });

  it("rejects a nested object in place of a scalar field", () => {
    expect(contactFormSchema.safeParse(validInput({ name: { first: "Ada" } })).success).toBe(false);
  });

  it("rejects an array in place of a scalar field", () => {
    expect(contactFormSchema.safeParse(validInput({ message: ["hello"] })).success).toBe(false);
  });

  it("rejects a file/blob-shaped value", () => {
    expect(contactFormSchema.safeParse(validInput({ message: new Uint8Array([1, 2, 3]) })).success).toBe(false);
  });
});

describe("contactFormSchema: length boundaries", () => {
  it("accepts a name exactly at the maximum length, rejects one character over", () => {
    expect(contactFormSchema.safeParse(validInput({ name: "a".repeat(CONTACT_NAME_MAX_LENGTH) })).success).toBe(true);
    expect(contactFormSchema.safeParse(validInput({ name: "a".repeat(CONTACT_NAME_MAX_LENGTH + 1) })).success).toBe(
      false,
    );
  });

  it("accepts a workEmail exactly at the maximum length, rejects one character over", () => {
    const localPartLength = CONTACT_EMAIL_MAX_LENGTH - "@example.com".length;
    const maxEmail = `${"a".repeat(localPartLength)}@example.com`;
    expect(maxEmail).toHaveLength(CONTACT_EMAIL_MAX_LENGTH);
    expect(contactFormSchema.safeParse(validInput({ workEmail: maxEmail })).success).toBe(true);
    expect(contactFormSchema.safeParse(validInput({ workEmail: `a${maxEmail}` })).success).toBe(false);
  });

  it("accepts a company exactly at the maximum length, rejects one character over", () => {
    expect(contactFormSchema.safeParse(validInput({ company: "a".repeat(CONTACT_COMPANY_MAX_LENGTH) })).success).toBe(
      true,
    );
    expect(
      contactFormSchema.safeParse(validInput({ company: "a".repeat(CONTACT_COMPANY_MAX_LENGTH + 1) })).success,
    ).toBe(false);
  });

  it("accepts a message exactly at the maximum length, rejects one character over", () => {
    expect(
      contactFormSchema.safeParse(validInput({ message: "a".repeat(CONTACT_MESSAGE_MAX_LENGTH) })).success,
    ).toBe(true);
    expect(
      contactFormSchema.safeParse(validInput({ message: "a".repeat(CONTACT_MESSAGE_MAX_LENGTH + 1) })).success,
    ).toBe(false);
  });

  it("accepts a turnstileToken exactly at the maximum length, rejects one character over", () => {
    expect(
      contactFormSchema.safeParse(validInput({ turnstileToken: "a".repeat(CONTACT_TURNSTILE_TOKEN_MAX_LENGTH) }))
        .success,
    ).toBe(true);
    expect(
      contactFormSchema.safeParse(validInput({ turnstileToken: "a".repeat(CONTACT_TURNSTILE_TOKEN_MAX_LENGTH + 1) }))
        .success,
    ).toBe(false);
  });

  it("never silently truncates an over-length value", () => {
    const overLong = "a".repeat(CONTACT_MESSAGE_MAX_LENGTH + 50);
    const result = contactFormSchema.safeParse(validInput({ message: overLong }));
    // Rejected outright -- not accepted with a truncated `data.message`.
    expect(result.success).toBe(false);
  });
});

describe("contactFormSchema: work email validity", () => {
  it("accepts a well-formed email, trimmed", () => {
    const result = contactFormSchema.safeParse(validInput({ workEmail: "  ada@example.com  " }));
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.workEmail).toBe("ada@example.com");
    }
  });

  it("rejects a string with no @", () => {
    expect(contactFormSchema.safeParse(validInput({ workEmail: "not-an-email" })).success).toBe(false);
  });

  it("rejects an email with no domain", () => {
    expect(contactFormSchema.safeParse(validInput({ workEmail: "ada@" })).success).toBe(false);
  });

  it("rejects a non-string workEmail", () => {
    expect(contactFormSchema.safeParse(validInput({ workEmail: 12345 })).success).toBe(false);
  });
});

describe("contactFormSchema: consent", () => {
  it("requires the literal boolean true", () => {
    expect(contactFormSchema.safeParse(validInput({ consent: true })).success).toBe(true);
  });

  it("rejects false", () => {
    expect(contactFormSchema.safeParse(validInput({ consent: false })).success).toBe(false);
  });

  it("rejects the string 'true'", () => {
    expect(contactFormSchema.safeParse(validInput({ consent: "true" })).success).toBe(false);
  });

  it("rejects a missing consent field", () => {
    const { consent: _consent, ...rest } = validInput();
    expect(contactFormSchema.safeParse(rest).success).toBe(false);
  });

  it("rejects a truthy non-boolean value", () => {
    expect(contactFormSchema.safeParse(validInput({ consent: 1 })).success).toBe(false);
  });
});

describe("contactFormSchema: control-character handling", () => {
  it("rejects a tab in a single-line field (name)", () => {
    expect(contactFormSchema.safeParse(validInput({ name: "Ada\tLovelace" })).success).toBe(false);
  });

  it("rejects a newline in a single-line field (name)", () => {
    expect(contactFormSchema.safeParse(validInput({ name: "Ada\nLovelace" })).success).toBe(false);
  });

  it("rejects a newline in company", () => {
    expect(contactFormSchema.safeParse(validInput({ company: "Acme\nInc" })).success).toBe(false);
  });

  it("rejects a null byte anywhere", () => {
    expect(contactFormSchema.safeParse(validInput({ name: "Ada\x00Lovelace" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ message: "hello\x00world" })).success).toBe(false);
  });

  it("allows an ordinary newline, carriage return, and tab in the message field", () => {
    expect(contactFormSchema.safeParse(validInput({ message: "line one\nline two\r\nindented:\tvalue" })).success).toBe(
      true,
    );
  });

  it("rejects a vertical tab or form feed in the message field", () => {
    expect(contactFormSchema.safeParse(validInput({ message: "line one\x0Bline two" })).success).toBe(false);
    expect(contactFormSchema.safeParse(validInput({ message: "line one\x0Cline two" })).success).toBe(false);
  });

  it("rejects DEL in the message field", () => {
    expect(contactFormSchema.safeParse(validInput({ message: "hello\x7Fworld" })).success).toBe(false);
  });
});

describe("contactFormSchema: input immutability", () => {
  it("does not mutate the object passed to safeParse", () => {
    const input = validInput({ name: "  Ada Lovelace  " });
    const snapshot = JSON.stringify(input);
    contactFormSchema.safeParse(input);
    expect(JSON.stringify(input)).toBe(snapshot);
  });
});

describe("contactFormSchema: HTML-shaped text stays an ordinary string", () => {
  it("accepts HTML/Markdown/URL-shaped text in the message field as a plain string, never interpreted", () => {
    const htmlLike = "<script>alert(1)</script> [link](javascript:alert(1)) https://example.com/?x=1";
    const result = contactFormSchema.safeParse(validInput({ message: htmlLike }));
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.message).toBe(htmlLike);
      expect(typeof result.data.message).toBe("string");
    }
  });
});
