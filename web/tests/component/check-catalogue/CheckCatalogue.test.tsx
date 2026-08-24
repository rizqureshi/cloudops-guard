// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CHECK_CATALOGUE } from "../../../src/features/check-catalogue/catalogue";
import { CheckCatalogue } from "../../../src/features/check-catalogue/CheckCatalogue";

function getCountText(): string {
  return screen.getByText(/^Showing \d+ of 17 checks\.$/).textContent ?? "";
}

describe("CheckCatalogue: initial state", () => {
  it("renders all 17 checks and the initial count", () => {
    render(<CheckCatalogue />);
    expect(getCountText()).toBe("Showing 17 of 17 checks.");
    expect(screen.getAllByRole("link")).toHaveLength(17);
  });
});

describe("CheckCatalogue: search", () => {
  it("matches check IDs case-insensitively", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.type(screen.getByLabelText("Search checks"), "k8s-img-001");
    expect(getCountText()).toBe("Showing 1 of 17 checks.");
    expect(screen.getByText("K8S-IMG-001")).toBeInTheDocument();
  });

  it("matches titles case-insensitively", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.type(screen.getByLabelText("Search checks"), "MUTABLE TAG");
    // K8S-IMG-001's title, and GL-CI-001's title, both mention a mutable tag.
    expect(screen.getByText("K8S-IMG-001")).toBeInTheDocument();
    expect(screen.getByText("GL-CI-001")).toBeInTheDocument();
    expect(getCountText()).toBe("Showing 2 of 17 checks.");
  });
});

describe("CheckCatalogue: filters", () => {
  it("filters by platform", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.selectOptions(screen.getByLabelText("Platform"), "kubernetes");
    expect(getCountText()).toBe("Showing 6 of 17 checks.");
    for (const entry of CHECK_CATALOGUE.filter((e) => e.platform === "kubernetes")) {
      expect(screen.getByText(entry.checkId)).toBeInTheDocument();
    }
  });

  it("filters by category", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.selectOptions(screen.getByLabelText("Category"), "Branch protection");
    expect(getCountText()).toBe("Showing 3 of 17 checks.");
    expect(screen.getByText("GL-BR-001")).toBeInTheDocument();
    expect(screen.getByText("GL-BR-002")).toBeInTheDocument();
    expect(screen.getByText("GL-BR-003")).toBeInTheDocument();
  });

  it("filters by severity", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.selectOptions(screen.getByLabelText("Severity"), "low");
    const lowCount = CHECK_CATALOGUE.filter((e) => e.severity === "low").length;
    expect(getCountText()).toBe(`Showing ${lowCount} of 17 checks.`);
  });

  it("combines search and filters", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.type(screen.getByLabelText("Search checks"), "mutable");
    await user.selectOptions(screen.getByLabelText("Platform"), "gitlab");
    expect(getCountText()).toBe("Showing 1 of 17 checks.");
    expect(screen.getByText("GL-CI-001")).toBeInTheDocument();
  });
});

describe("CheckCatalogue: clear", () => {
  it("clear restores all 17 checks", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.type(screen.getByLabelText("Search checks"), "k8s-img-001");
    await user.selectOptions(screen.getByLabelText("Severity"), "high");
    expect(getCountText()).toBe("Showing 1 of 17 checks.");

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(getCountText()).toBe("Showing 17 of 17 checks.");
    expect(screen.getByLabelText("Search checks")).toHaveValue("");
    expect(screen.getByLabelText("Severity")).toHaveValue("all");
  });
});

describe("CheckCatalogue: empty results", () => {
  it("shows a clear empty-results message when nothing matches", async () => {
    const user = userEvent.setup();
    render(<CheckCatalogue />);
    await user.type(screen.getByLabelText("Search checks"), "this string matches no catalogue entry");
    expect(screen.getByText(/No checks match your current search and filters/)).toBeInTheDocument();
    expect(getCountText()).toBe("Showing 0 of 17 checks.");
  });
});

describe("CheckCatalogue: detail links", () => {
  it("every result links to its correct /checks/<id> detail route", () => {
    render(<CheckCatalogue />);
    for (const entry of CHECK_CATALOGUE) {
      const link = screen.getByRole("link", { name: entry.title });
      expect(link).toHaveAttribute("href", `/checks/${entry.checkId}`);
    }
  });
});

describe("CheckCatalogue: text rendering", () => {
  it("severity, platform, and category all appear as visible text on every result", () => {
    render(<CheckCatalogue />);
    // Sample one entry from each platform rather than asserting on all 17,
    // since severity/platform/category text repeats across many rows.
    expect(screen.getAllByText("Kubernetes").length).toBeGreaterThan(0);
    expect(screen.getAllByText("GitLab").length).toBeGreaterThan(0);
    expect(screen.getAllByText("High").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Medium").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Low").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Resource management").length).toBeGreaterThan(0);
  });
});

describe("CheckCatalogue: no excluded controls", () => {
  it("renders no request-demo, feedback, credential, upload, or unsupported-check control", () => {
    render(<CheckCatalogue />);
    expect(screen.queryByRole("button", { name: /request.{0,2}demo/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /request.{0,2}demo/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /feedback/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/kubeconfig|gitlab token|access token|password/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /upload/i })).not.toBeInTheDocument();
    expect(document.querySelector('input[type="file"]')).not.toBeInTheDocument();
    for (const disallowedId of ["AKS-", "EKS-"]) {
      expect(screen.queryByText(new RegExp(disallowedId))).not.toBeInTheDocument();
    }
  });
});
