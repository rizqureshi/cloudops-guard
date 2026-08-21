// @ts-check
import js from "@eslint/js";
import astroPlugin from "eslint-plugin-astro";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactPlugin from "eslint-plugin-react";
import reactHooksPlugin from "eslint-plugin-react-hooks";
import globals from "globals";
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";

// Flat ESLint config for the CloudOps Guard web foundation (v0.3.0 Phase 3B).
//
// `eslint-plugin-astro`'s `flat/jsx-a11y-recommended` preset already covers
// `.astro` files (parsing, its own recommended rules, and accessibility
// rules for Astro markup) -- it is used instead of `flat/recommended` so
// `.astro` templates get the same accessibility linting as TSX components,
// consistent with this project's WCAG 2.2 AA target.
export default [
  {
    ignores: ["dist/**", ".astro/**", "node_modules/**"],
  },

  js.configs.recommended,
  ...astroPlugin.configs["flat/jsx-a11y-recommended"],

  // TypeScript (and TSX) source.
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.es2024,
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
      "jsx-a11y": jsxA11y,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      ...reactPlugin.configs.flat.recommended.rules,
      ...reactPlugin.configs.flat["jsx-runtime"].rules,
      ...reactHooksPlugin.configs.flat["recommended-latest"].rules,
      ...jsxA11y.flatConfigs.recommended.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
    settings: {
      react: { version: "19.2.8" },
    },
  },

  // Project config files (astro.config.mjs, eslint.config.js) run under
  // Node, not the browser.
  {
    files: ["*.mjs", "*.js", "*.cjs"],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
];
