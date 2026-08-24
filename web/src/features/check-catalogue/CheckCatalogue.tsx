import { useMemo, useState } from "react";

import { deriveCategory } from "../report-workspace/category";
import { CHECK_CATALOGUE } from "./catalogue";
import "./check-catalogue.css";
import {
  DEFAULT_CATALOGUE_FILTER_STATE,
  distinctCatalogueCategories,
  filterCatalogueEntries,
  type CatalogueFilterState,
} from "./filtering";
import type { CheckPlatform } from "./types";

const PLATFORM_OPTIONS: readonly CheckPlatform[] = ["kubernetes", "gitlab"];
const SEVERITY_OPTIONS = ["critical", "high", "medium", "low"] as const;

const PLATFORM_LABELS: Readonly<Record<CheckPlatform, string>> = {
  kubernetes: "Kubernetes",
  gitlab: "GitLab",
};

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * The searchable check catalogue island (`/checks`, Phase 3H).
 *
 * Renders every entry from the project-owned `CHECK_CATALOGUE` (see
 * `./catalogue.ts`) -- never data derived from a synthetic or imported
 * report. All filtering happens client-side, in React memory only: no
 * `fetch`/`XMLHttpRequest`, no `localStorage`/`sessionStorage`/cookies/
 * IndexedDB, no URL query-string/fragment state, and no analytics.
 */
export function CheckCatalogue() {
  const [filters, setFilters] = useState<CatalogueFilterState>(DEFAULT_CATALOGUE_FILTER_STATE);

  const categoryOptions = useMemo(() => distinctCatalogueCategories(CHECK_CATALOGUE), []);
  const filtered = useMemo(() => filterCatalogueEntries(CHECK_CATALOGUE, filters), [filters]);

  const totalCount = CHECK_CATALOGUE.length;
  const filteredCount = filtered.length;

  function clearFilters(): void {
    setFilters(DEFAULT_CATALOGUE_FILTER_STATE);
  }

  return (
    <div className="check-catalogue">
      <form className="check-catalogue__controls" onSubmit={(event) => event.preventDefault()}>
        <div className="check-catalogue__field">
          <label htmlFor="catalogue-search">Search checks</label>
          <input
            id="catalogue-search"
            type="search"
            value={filters.search}
            onChange={(event) => setFilters((previous) => ({ ...previous, search: event.target.value }))}
            placeholder="Search check ID or title…"
          />
        </div>

        <div className="check-catalogue__field">
          <label htmlFor="catalogue-platform-filter">Platform</label>
          <select
            id="catalogue-platform-filter"
            value={filters.platform}
            onChange={(event) =>
              setFilters((previous) => ({
                ...previous,
                platform: event.target.value as CatalogueFilterState["platform"],
              }))
            }
          >
            <option value="all">All platforms</option>
            {PLATFORM_OPTIONS.map((platform) => (
              <option key={platform} value={platform}>
                {PLATFORM_LABELS[platform]}
              </option>
            ))}
          </select>
        </div>

        <div className="check-catalogue__field">
          <label htmlFor="catalogue-category-filter">Category</label>
          <select
            id="catalogue-category-filter"
            value={filters.category}
            onChange={(event) =>
              setFilters((previous) => ({
                ...previous,
                category: event.target.value as CatalogueFilterState["category"],
              }))
            }
          >
            <option value="all">All categories</option>
            {categoryOptions.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </select>
        </div>

        <div className="check-catalogue__field">
          <label htmlFor="catalogue-severity-filter">Severity</label>
          <select
            id="catalogue-severity-filter"
            value={filters.severity}
            onChange={(event) =>
              setFilters((previous) => ({
                ...previous,
                severity: event.target.value as CatalogueFilterState["severity"],
              }))
            }
          >
            <option value="all">All severities</option>
            {SEVERITY_OPTIONS.map((severity) => (
              <option key={severity} value={severity}>
                {capitalize(severity)}
              </option>
            ))}
          </select>
        </div>

        <button type="button" className="check-catalogue__clear" onClick={clearFilters}>
          Clear filters
        </button>
      </form>

      <p className="check-catalogue__count" aria-live="polite">
        Showing {filteredCount} of {totalCount} checks.
      </p>

      {filtered.length === 0 ? (
        <p className="check-catalogue__empty">
          No checks match your current search and filters. Clear them to see all {totalCount} checks.
        </p>
      ) : (
        <ul className="check-catalogue__results">
          {filtered.map((entry) => (
            <li key={entry.checkId} className="check-catalogue__item">
              <div className="check-catalogue__item-header">
                <span className={`status-label status-label--${entry.severity}`}>{capitalize(entry.severity)}</span>
                <span className="check-catalogue__check-id">{entry.checkId}</span>
                <span className="check-catalogue__platform">{PLATFORM_LABELS[entry.platform]}</span>
                <span className="check-catalogue__category">{deriveCategory(entry.checkId)}</span>
              </div>
              <a className="check-catalogue__title" href={`/checks/${entry.checkId}`}>
                {entry.title}
              </a>
              <p className="check-catalogue__trigger">{entry.triggerCondition}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
