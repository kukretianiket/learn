# Rendering contract

- Every mathematical token—including a lone variable, subscript, superscript, Greek letter, operator, or equation in prose, bullets, tables, callouts, and options—must be inside `$...$` or `$$...$$`. Parentheses are punctuation, never math delimiters: write `($n_{\mathrm{eff}}$)`, not `(n_{\mathrm{eff}})`.
- Inline math uses exactly one `$` on each side, remains on one physical Markdown line, and has no padding spaces: `$x_i$`, not `$ x_i $`. In lesson Markdown, do not use `\(...\)` or `\[...\]`, and do not put renderable math in backticks.
- Display math uses delimiter-only lines. Write `$$`, then the expression, then `$$`; never write `$$ E = mc^2 $$`. In a callout, quote every line: `> $$`, `> E = mc^2`, `> $$`.
- Use MathJax-compatible LaTeX with explicit braces and separators. Write `$n_{\mathrm{eff}}$`, `$\lambda_0$`, `$\operatorname{softmax}(x)$`, and `$\phi = \frac{2\pi n_{\mathrm{eff}}L}{\lambda_0}$`. Reject `n{\text{eff}}`, `\lambda0`, `\sqrt2`, missing braces, or a command run into a digit.
- Do not put `\displaystyle` inside inline math. Use a display block for a fraction or aligned expression that genuinely needs display sizing. Avoid unsupported LaTeX packages and commands; use core MathJax syntax only.
- In a Markdown table, keep each formula within one cell and avoid a raw `|` inside math; use `\vert` when a vertical bar is mathematical. Mermaid labels do not render MathJax reliably, so use short plain-language or Unicode labels there and explain formal notation outside the diagram.
- Preserve raw Markdown: code fences, tables, callouts, Mermaid, and display math must remain unwrapped.
- Use native Obsidian callouts: `[!question]`, `[!hint]`, `[!failure]`, `[!success]`, `[!definition]`, and `[!warning]`.
- Put generated `.svg` and `.png` files only in the configured learning folder's `Assets` directory (normally `Learning/Assets`) and embed them as `![[Assets/name.ext]]` (optionally with a width).
- Link references with ordinary Markdown links or `[[Sources/<citekey>]]`.
- Never put model citation tokens, tool traces, shell output, or internal process commentary in the lesson.

## Choose and teach with a visual

At the route and at a difficult conceptual step, consider what the learner needs to see: relationships, a process over time, geometry, quantities, or a concrete worked trace. Use a visual when it makes that relationship easier to infer than prose. A route map does not replace a mechanism diagram, and a decorative image does not satisfy this purpose.

- Use native Mermaid for a small dependency map, flow, sequence, or state transition. Keep it readable in the half-width Obsidian pane.
- For geometry or quantitative behavior, use a small accurate SVG or a plot generated with available tools. Label axes, units, conditions, and illustrative versus measured data. Use existing capabilities; do not install a rendering stack for the lesson.
- When an authoritative source already has a better diagram, inspect the actual figure and caption, then provide a descriptive Markdown link to its real figure/section location with attribution. State what to notice and how it connects to the current explanation. If you cannot inspect the image, label it as an uninspected optional resource rather than describing its contents as checked.
- Prefer linking to an external figure over copying it into the vault unless permission or license allows reuse. Cite the source for an adapted figure and identify your simplifications. Save generated local assets only in the configured Learning folder's `Assets` directory; honor a custom learning-folder name.

Before showing a generated visual, check arrow direction, labels, scale, and the assumptions it implies. When an available renderer can produce an image, render and inspect it and repair unreadable or incorrect output. If visual inspection is unavailable, use simple established syntax and describe validation honestly; never claim to have viewed a render you did not inspect. A complicated visual that cannot be checked should be simplified or replaced with an inspected source link and a self-contained explanation.

Introduce the visual with its purpose, explain the one relationship it reveals, and use it for a prediction or comparison when that helps learning. For a discovery task, reveal only the setup before the learner predicts; show the explanatory result afterward. Do not expose an answer through an image or caption while claiming to test unaided recall. If a figure adds no explanatory value, omit it; substantial lessons still retain the required small route map.

## Mermaid concept maps

Adapt the upstream Pi visualization discipline (repository source: `pi/skills/visualize/SKILL.md`): one visual should carry one structural idea with the fewest necessary elements. A substantial lesson gets a small local concept map, not a global graph and not decoration.

- Use `flowchart TD` for lesson concept maps. Do not use `LR` or `RL`; the half-screen Obsidian pane must not require horizontal scrolling.
- Prefer one short vertical main chain, at most about 5–7 nodes, short quoted labels, and only edges that teach something. Prune any node whose removal leaves the idea intact.
- Do not assume the knowledge structure is a DAG. Prerequisites can form the main directed spine, while labeled cross-links, detours, and feedback edges may loop back when that relationship is real. Never delete a meaningful loop merely to make the graph acyclic.
- Keep edge meaning explicit: distinguish “requires”, “explains”, “applies to”, and “feeds back” where ambiguity would change the learner's model.
- If true cycles or cross-links make the result tangled, simplify to the main vertical chain and explain secondary links in prose. Do not falsify the structure to obtain a tidy layout.
- Use stable Mermaid syntax and quoted labels. If Mermaid CLI is already installed, use `learnctl validate --render-mermaid`; never install it or Chromium as a Learn dependency.

Other stable types such as `sequenceDiagram` and `stateDiagram-v2` are appropriate when sequence or state—not concept relationships—is the actual idea. Do not create a separate visual-maker workflow.

## Pre-send math audit

Before every response containing math:

1. Ignore fenced and inline code, then scan every remaining character for a backslash command, `_`, or `^` outside math delimiters.
2. Reject every `\(`, `\)`, `\[`, `\]`, bare parenthesized formula, padded inline delimiter, multi-line inline span, or same-line display delimiter.
3. Check `$` pairs independently on every physical line. Check that each `$$` is alone on its line and has one closing delimiter.
4. Check braces, command spelling, explicit subscripts, and text operators. Use an actual MathJax render check when the environment provides one; structural patterns are not a general LaTeX parser. Specifically reject forms like `\lambda0`, `\sqrt2`, and `n{\text{eff}}`.
5. Audit every list item, table cell, callout line, and diagnostic option—not only prose paragraphs. A valid option is `A. $\phi = \frac{2\pi n_{\mathrm{eff}}L}{\lambda_0}$`.
6. Mentally read the final raw Markdown as Obsidian receives it. Do not rely on how the Codex terminal happened to render the draft.

After syntax renders, review the mathematics separately: verify that every symbol denotes the intended quantity, every operator is the intended operator, and each expression supports the stated claim. For example, a slash accidentally typed where a backslash was intended may remain syntactically renderable while expressing something else. Rendering success is not mathematical validation, and prose quality is not factual validation.
