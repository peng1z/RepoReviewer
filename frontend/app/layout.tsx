import type { Metadata } from "next";
import "./globals.css";

const SITE = "https://reporeviewer.peng1z.workers.dev";
const PAPER = "https://arxiv.org/abs/2603.16107";
const REPO = "https://github.com/peng1z/RepoReviewer";

const DESCRIPTION =
  "A recorded multi-agent review of psf/requests, with every finding checked " +
  "against the code: 65 findings, a positional check on all of them, and a " +
  "by-hand check of the 8 high-severity ones. Artifact for arXiv:2603.16107.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: "RepoReviewer — a checked multi-agent code review of psf/requests",
  description: DESCRIPTION,
  authors: [{ name: "Peng Zhang" }],
  keywords: [
    "code review",
    "multi-agent",
    "large language models",
    "repository-level",
    "software engineering",
    "LLM evaluation",
    "false positives",
  ],
  alternates: { canonical: SITE },
  openGraph: {
    type: "website",
    url: SITE,
    siteName: "RepoReviewer",
    title: "RepoReviewer — a checked multi-agent code review of psf/requests",
    description: DESCRIPTION,
  },
  twitter: {
    card: "summary_large_image",
    title: "RepoReviewer — a checked multi-agent code review of psf/requests",
    description: DESCRIPTION,
  },
};

/**
 * Structured data describing the *software*, and naming the article it
 * accompanies.
 *
 * Deliberately not Google Scholar's `citation_*` meta tags. Those assert that
 * the page they sit on is the article, and Scholar's guidelines say it "does
 * not index pages that merely describe or link to papers". This page is the
 * artifact, not the paper; arXiv:2603.16107 is the article and already carries
 * those tags. Claiming otherwise here would be a false statement to a parser
 * that has no way to check it.
 */
const STRUCTURED_DATA = {
  "@context": "https://schema.org",
  "@type": "SoftwareSourceCode",
  name: "RepoReviewer",
  description:
    "A local-first multi-agent system that reviews a GitHub repository or " +
    "pull request: clone, build project context, review files, prioritise " +
    "findings, summarise. Runs on the user's machine against their own " +
    "provider key.",
  url: SITE,
  codeRepository: REPO,
  programmingLanguage: ["Python", "TypeScript"],
  runtimePlatform: ["Python 3.11+", "Node.js 20+"],
  license: "https://opensource.org/licenses/MIT",
  author: {
    "@type": "Person",
    name: "Peng Zhang",
    url: "https://github.com/peng1z",
  },
  citation: {
    "@type": "ScholarlyArticle",
    name:
      "RepoReviewer: A Local-First Multi-Agent Architecture for " +
      "Repository-Level Code Review",
    author: { "@type": "Person", name: "Peng Zhang" },
    datePublished: "2026-03-17",
    identifier: "arXiv:2603.16107",
    url: PAPER,
    sameAs: PAPER,
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <script
          type="application/ld+json"
          // The payload is a literal in this file, not user or model input.
          dangerouslySetInnerHTML={{ __html: JSON.stringify(STRUCTURED_DATA) }}
        />
        {children}
      </body>
    </html>
  );
}
