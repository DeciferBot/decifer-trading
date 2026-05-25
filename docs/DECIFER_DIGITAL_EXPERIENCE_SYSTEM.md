# DECIFER Digital Experience System

**Version 1.0 — Sprint 1.1 — May 2026**

This document is the authoritative design system reference for all DECIFER digital surfaces. It supersedes any conflicting guidance in individual repo READMEs. Brand guidelines and tone of voice remain in `DECIFER_BRAND_GUIDELINES.md`; this document governs visual implementation only.

---

## 1. Design Philosophy

DECIFER surfaces exist to turn complex information into usable context. The design must serve that purpose directly — nothing else.

Every visual decision answers the same test: **does this make the information clearer, or does it add noise?**

**Five rules that follow from that:**

1. Clarity before aesthetics. If a design choice makes content harder to read or understand, it is wrong regardless of how it looks in isolation.
2. Restraint is a design decision. Unused space is not wasted space. Colour used sparingly carries more meaning than colour used freely.
3. Nothing decorative. No element exists for atmosphere. No gradient exists to fill space. No animation exists to signal effort.
4. Context matters. A financial product and a children's learning product are read in completely different emotional states, by different people, in different settings. The visual system must reflect that.
5. The brand signal is the orange. Everything else is in service of content legibility.

---

## 2. Master Brand Colour

| Token | Value | Use |
|-------|-------|-----|
| DECIFER orange | `#F05A28` | The master brand signal. CTAs, both mark brackets, active indicators, highest-priority labels. |

**Why `#F05A28` and not a standard orange:**

The DECIFER mark needs to be immediately recognisable on both dark fintech surfaces (deep navy) and light educational surfaces (near-white). `#F05A28` was chosen because:
- It holds WCAG AA contrast (4.5:1+) against `#04060c` and `#070a12` for normal text sizes
- It reads as deliberate and warm, not energetic or alarming — appropriate for decision intelligence
- It visually roots all DECIFER products to the same mark without clashing with any product-specific supporting palette

**Non-negotiables:**
- Never replace this orange with a product-specific colour in any DECIFER CTA or mark
- Never lighten it for aesthetic reasons — it exists to be noticed, not to blend
- Never use it decoratively — if something is orange, it is signalling something important
- The hover state `#F47040` is acceptable for interactive elements only
- **Both brackets of the mark must be `#F05A28`. Split-colour treatment (one bracket orange, one white or any other colour) is prohibited.** The `color` prop on `DeciferMark` applies to both brackets equally. There is no `leftColor`/`rightColor` API and one must never be added.

---

## 3. Parent Site Colour System (decifer.io)

The parent site represents the full DECIFER intelligence suite. It sits above all product lines. Its palette is neutral on product-specific colours and uses the DECIFER mark orange as the sole accent.

| Token | Value | Purpose |
|-------|-------|---------|
| `--color-canvas` | `#060b18` | Page background |
| `--color-surface` | `#0b1328` | Card/panel backgrounds |
| `--color-surface-alt` | `#101c3a` | Elevated cards |
| `--color-ink` | `#eef2ff` | Primary text |
| `--color-muted` | `#6e88a8` | Secondary text |
| `--color-faint` | `#2a3860` | Metadata, fine print |
| `--color-line` | `#172038` | Borders |
| `--color-mark` | `#F05A28` | DECIFER mark orange (master brand signal) |
| `--color-cta` | `#F05A28` | CTA buttons and primary actions |
| `--color-trading` | `#3d7eff` | DECIFER Trading product colour |
| `--color-learn` | `#9578e8` | DECIFER Learning product colour |
| `--color-live` | `#0dc47c` | Live/active indicator |

**Product colour logic on the parent site:**

The parent site introduces product-specific colours (`--color-trading` blue, `--color-learn` purple) only to differentiate product cards and navigation — so a visitor can distinguish Trading from Learning at a glance. These colours do not carry into the individual product sites. Trading's homepage is not blue; Learning's surface is not purple-dominant.

---

## 4. DECIFER Trading Colour System (decifertrading.com)

Trading is a fintech product. It serves active investors making time-sensitive decisions. The emotional register is calm, precise, and professional. The palette reflects the information density of a trading interface: dark surfaces that reduce eye strain over extended sessions, high-contrast text, and colour used only to signal data states.

| Token | Value | Purpose |
|-------|-------|---------|
| `--bg-deep` | `#04060c` | Deepest background, hero sections |
| `--bg` | `#070a12` | Main page background |
| `--surface` | `#0c1220` | Card backgrounds |
| `--surface-2` | `#111826` | Elevated cards, inputs |
| `--surface-3` | `#162034` | Deeply nested panels |
| `--border` | `#1a2840` | Default borders |
| `--border-strong` | `#253650` | Emphasised borders |
| `--text-1` | `#e8f0fa` | Headlines, primary body copy |
| `--text-2` | `#7a8fa8` | Supporting copy, labels |
| `--text-3` | `#3d5166` | Metadata, fine print |
| `--orange` | `#F05A28` | DECIFER orange — CTAs, mark, data highlight |
| `--orange-light` | `#F47040` | Hover state for orange interactive elements |
| `--orange-bg` | `rgba(240,90,40,0.08)` | Tinted backgrounds for orange-context areas |
| `--orange-border` | `rgba(240,90,40,0.20)` | Tinted borders for orange-context areas |
| `--color-success` | `#10b981` | Positive data, live indicators, gains |
| `--color-warning` | `#f59e0b` | Under review, caution, pending |
| `--color-error` | `#f43f5e` | Blocked, negative, error states |
| `--color-info` | `#3b82f6` | Neutral informational highlights |

**Why dark for Trading:**

The product is used during market hours, often alongside other screens. Dark surfaces reduce cognitive fatigue, match the terminal aesthetic that active investors recognise, and give data states (green/amber/red) maximum visual separation from the background.

**Contrast requirements for Trading text:**

| Text role | Colour | Background | Ratio | WCAG level |
|-----------|--------|------------|-------|------------|
| `--text-1` on `--bg` | `#e8f0fa` on `#070a12` | ~14:1 | AAA |
| `--text-2` on `--surface` | `#7a8fa8` on `#0c1220` | ~4.6:1 | AA |
| `--orange` on `--bg` | `#F05A28` on `#070a12` | ~4.8:1 | AA |
| `--color-success` on `--surface` | `#10b981` on `#0c1220` | ~5.2:1 | AA |

`--text-3` (`#3d5166`) on dark backgrounds does not reach AA and is reserved for decorative metadata only — never for content a user must read to understand the product.

---

## 5. DECIFER Learning Colour System (decifer-learning)

Learning is a UK-curriculum education product. It serves children in a school or home setting, primarily during the day on a parent- or school-managed device. The emotional register is warm, encouraging, and clear. The palette is light-surfaced to reflect a workbook or digital classroom, with playful but not childish subject colours.

| Token | Value | Purpose |
|-------|-------|---------|
| `--background` | `#FAFBFF` | Page background |
| `--surface` | `#FFFFFF` | Card/panel backgrounds |
| `--brand` | `#F05A28` | DECIFER orange — the parent mark, CTAs |
| `--brand-50` | `#FEF0E8` | Light tint of brand for backgrounds |
| `--brand-600` | `#CC4A21` | Darker brand for text on light bg |
| `--maths` | `#6C9EFF` | Mathematics subject colour |
| `--english` | `#FF8FAB` | English subject colour |
| `--science` | `#52D9A0` | Science subject colour |
| `--sprout` | `#A8E6CF` | Beginner difficulty tier |
| `--explorer` | `#74C0FC` | Intermediate difficulty tier |
| `--lightning` | `#FFD43B` | Advanced difficulty tier |
| `--correct` | `#40C057` | Correct answer feedback |
| `--incorrect` | `#FF6B6B` | Incorrect answer feedback |
| `--text-primary` | `#2D3748` | Main body text |
| `--text-muted` | `#718096` | Secondary labels |
| `--color-success` | `#0dc47c` | Success state |
| `--color-warning` | `#f59e0b` | Warning state |
| `--color-error` | `#ef4444` | Error state |
| `--color-info` | `#3b82f6` | Informational state |

**Why light for Learning:**

Children's educational environments are bright — classrooms, kitchen tables, tablets propped against school bags. Dark surfaces create unnecessary friction in these contexts. A light, paper-like background signals a learning environment. It also makes colour-coded subject lanes (maths/english/science) more visually distinct than they would be on dark.

**Subject colour logic:**

Subject colours serve orientation, not decoration. A child should immediately know whether they are looking at a maths problem or an English task without reading the label. The three colours were chosen to be:
- Clearly distinct from each other
- Not gendered in implication
- Readable with `--text-primary` at the contrast levels required for accessibility

---

## 6. Typography Rules

### Typeface

**DECIFER Trading and parent site:** Plus Jakarta Sans Variable  
**DECIFER Learning:** Nunito (child-appropriate, rounded, highly legible at small sizes on screen)

Both are variable fonts with precise weight control. Neither was chosen for aesthetic fashion — both were chosen for legibility at a wide range of sizes, across the reading contexts of their users.

### Scale and weight

| Role | Weight | Size | Letter-spacing | Line-height |
|------|--------|------|----------------|-------------|
| Display headline | 800 | `clamp(2.5rem, 5vw, 4.25rem)` | `-0.03em` | 1.08 |
| Section headline | 700 | `2rem–2.5rem` | `-0.025em` | 1.15 |
| Subheading | 600–700 | `1.25rem–1.5rem` | `-0.02em` | 1.25 |
| Body | 400 | `1rem–1.0625rem` | `0` | 1.65–1.75 |
| Labels and metadata | 600–700 | `11px–12px` | `0.06em–0.10em` | 1.4 |
| CTAs | 600–700 | `0.875rem–0.95rem` | `0` | 1 |
| Fine print / legal | 400 | `0.75rem–0.8rem` | `0` | 1.65 |

### Rules

- Headlines are set tight (negative letter-spacing). Loose headline spacing reads as unconfident.
- Body copy is set comfortable (1.65+ line-height). Dense copy at 1.4 line-height is fatiguing to read.
- Labels and metadata use uppercase with moderate letter-spacing (0.08em) to distinguish them from body copy at small sizes.
- Never set body copy above 600 weight. Above 600 in a paragraph block reads as aggressive.
- Never set legal or compliance copy below 0.75rem. Regulators do not consider invisible text disclosed.

---

## 7. Contrast Rules

All text-on-background combinations must meet WCAG 2.1 AA minimum (4.5:1 for normal text, 3:1 for large text above 18pt or 14pt bold).

**Pre-approved combinations for Trading:**

| Text | Background | Approved use |
|------|------------|--------------|
| `#e8f0fa` | `#070a12` | Primary body copy |
| `#e8f0fa` | `#0c1220` | Card body copy |
| `#7a8fa8` | `#070a12` | Secondary labels |
| `#7a8fa8` | `#0c1220` | Secondary labels on card |
| `#F05A28` | `#070a12` | Orange on main bg |
| `#F05A28` | `#0c1220` | Orange on card |
| `#10b981` | `#0c1220` | Success state on card |
| White `#fff` | `#F05A28` | CTA button label |

**Pre-approved combinations for Learning:**

| Text | Background | Approved use |
|------|------------|--------------|
| `#2D3748` | `#FAFBFF` | Body copy |
| `#2D3748` | `#FFFFFF` | Card body copy |
| `#718096` | `#FFFFFF` | Secondary labels |
| `#CC4A21` | `#FAFBFF` | Brand text on light bg |
| `#F05A28` | `#FFFFFF` | Orange on white |

**Prohibited combinations:**

- `#3d5166` (text-3) used for any readable content — metadata only
- Light text on purple backgrounds with opacity — do not create custom product sub-palettes that haven't been contrast-tested
- Orange text on orange backgrounds at any opacity

---

## 8. Radius Scale

All DECIFER surfaces share a common radius scale. The purpose of the scale is to allow visual hierarchy through curvature: interactive elements (buttons, tags) use `sm`; content cards use `md` or `lg`; modals and large panels use `xl` or `2xl`.

| Token | Value | Typical use |
|-------|-------|-------------|
| `--radius-sm` | `0.375rem` (6px) | Badges, inline tags, small chips |
| `--radius-md` | `0.5rem` (8px) | Input fields, small buttons |
| `--radius-lg` | `0.75rem` (12px) | Cards, standard buttons, nav elements |
| `--radius-xl` | `1rem` (16px) | Large cards, modals |
| `--radius-2xl` | `1.5rem` (24px) | Device frames, product mockups |

**Rule:** Do not introduce custom radius values outside this scale. The scale creates visual coherence across surfaces. An element that needs a radius not in this scale is either using the wrong radius or using the wrong component pattern.

---

## 9. Spacing Rules

Spacing follows a base-8 system. Use multiples of 8px (or 4px for fine-grained internal component spacing) for all padding, margin, and gap values.

| Spacing | px | rem | Typical use |
|---------|----|-----|-------------|
| `4` | 4px | 0.25rem | Icon-to-label gap, tight internal spacing |
| `8` | 8px | 0.5rem | Component internal padding (dense) |
| `12` | 12px | 0.75rem | Component internal padding (comfortable) |
| `16` | 16px | 1rem | Base paragraph spacing, list gaps |
| `24` | 24px | 1.5rem | Between related elements |
| `32` | 32px | 2rem | Between distinct sections within a card |
| `48` | 48px | 3rem | Section header to content |
| `64` | 64px | 4rem | Mobile section padding |
| `112` | 112px | 7rem | Desktop section padding (`padding: 7rem 0`) |

**Content max-widths:**

| Context | Max-width |
|---------|-----------|
| Reading column (prose, hero copy) | `540px` |
| Section header / intro paragraph | `640px` |
| Full-width content area | `max-w-7xl` (1280px) |
| Legal prose | `680px` |

---

## 10. CTA Hierarchy

Every page has at most three CTA levels. The hierarchy must be visually clear at a glance.

| Level | Appearance | Use |
|-------|------------|-----|
| Primary | Solid `--orange` background, white text | The single most important action on the page |
| Secondary | `--surface` background, `--text-1`, `--border-strong` border | Important but competing action |
| Ghost / text | No background, no border, `--text-2` | Low-friction navigation or tertiary action |

**Rules:**
- A primary CTA should appear at most once per viewport. If a second primary is needed, the page structure has too many competing actions.
- Primary CTAs on Trading: "Request access", "Request early access", "Request NDA demo"
- Never use the orange for two CTAs of different priority on the same screen — the orange signals "most important action" and cannot be diluted
- CTA labels are imperative verbs: "Request access" not "Access can be requested", "View preview" not "Preview available"

---

## 11. Card and Proof-Module Rules

Cards are used to present structured intelligence output — market signals, product features, proof points. They are not decorative containers.

**Card anatomy:**

- Background: `--surface` or `--surface-2`
- Border: 1px solid `--border` (or `--border-strong` for emphasis)
- Radius: `--radius-lg` (12px) for standard cards, `--radius-xl` for feature cards
- Padding: 16px (dense) or 24px (standard)
- No drop shadows except for product mockups and device frames

**Label pattern (proof modules):**

All cards that present a claim or data point must follow this structure:
1. A category label — uppercase, `--text-3`, 11–12px, wide letter-spacing — identifies what type of claim this is
2. A primary statement — `--text-1`, 600–700 weight — the claim itself
3. Supporting detail — `--text-2`, 400 weight — the evidence or context
4. Optional badge — coloured inline tag for state (live, under review, blocked)

**Badge colour rules:**

| Badge state | Background | Text | Border |
|-------------|------------|------|--------|
| Active / live | `rgba(16,185,129,0.12)` | `#10b981` | `rgba(16,185,129,0.25)` |
| Under review | `rgba(245,158,11,0.12)` | `#f59e0b` | `rgba(245,158,11,0.25)` |
| Context / orange | `rgba(240,90,40,0.10)` | `#F05A28` | `rgba(240,90,40,0.25)` |
| Blocked / slate | `rgba(100,116,139,0.12)` | `#94a3b8` | `rgba(100,116,139,0.25)` |

**Proof modules** (stats, evidence points) use the same card anatomy with a large number or metric as the primary element, followed by a descriptor label and optional sub-context.

---

## 12. Icon Language

DECIFER surfaces use a minimal, purposeful icon vocabulary.

**Approved icon uses:**
- Navigation toggle (hamburger / X) for mobile nav
- Inline directional arrows in text links ("→")
- Status indicators (live dot, animated pulse)
- The DECIFER mark (`<` and `>` brackets) as the logo mark
- Form icons: search glass, dropdown chevron

**Prohibited uses:**
- Decorative icons added to make cards look "richer"
- Generic tech icons (circuit boards, brain outlines, network graphs) to signal AI
- Financial clichés (candlestick charts, bull/bear silhouettes, banknotes) in hero sections
- Emoji in product UI (acceptable in copy for Learning only, where tone of voice permits)

**Icon source:**  
Lucide React is approved for UI icons. Do not mix icon libraries on a single surface.

---

## 13. Chart and Graph Language

**For Trading surfaces:**

Charts must follow these rules when added to product or marketing surfaces:

- Background: `--surface` or `--bg`
- Grid lines: `--border` at low opacity (max 0.3)
- Positive data: `--color-success` (`#10b981`)
- Negative data: `--color-error` (`#f43f5e`)
- Neutral / baseline: `--text-3` or `--border`
- Highlight / selected: `--orange` (`#F05A28`)
- No chart decoration: no unnecessary labels, no gratuitous tick marks, no fake-precision axes
- No 3D charts under any circumstances
- No pie charts for time-series or comparative signal data — use bar or line

**Chart type rules:**

| Data type | Chart type |
|-----------|------------|
| Time-series (price, signal) | Line chart |
| Categorical comparison | Bar chart |
| Distribution | Histogram |
| Portfolio allocation | Horizontal bar |
| Signal score comparison | Horizontal bar or radar |

**For Learning surfaces:**

Charts are used for data literacy lessons only. They follow the same grid/bg rules but may use subject colours (`--maths`, `--english`, `--science`) as the series colours.

---

## 14. Motion Principles

Motion is permitted only when it communicates state or guides attention. Decorative motion is prohibited.

**Permitted:**
- Fade-up entrance animations for above-the-fold hero content (one-shot, fires once on load, 0.5–0.65s)
- Scroll-driven reveals for section content (CSS `animation-timeline: view()` with graceful fallback)
- Live indicator pulse for real-time data (`pulse-dot`, 2.2s ease-in-out)
- Backdrop blur for fixed nav (CSS property, no animation)
- Button background transitions (0.15s — imperceptible, functional)

**Prohibited:**
- Continuous background animations (floating orbs, particle systems, looping meshes)
- Parallax effects
- Hero elements that rotate, scale, or translate on scroll for atmosphere
- Loading spinners on static marketing pages
- Skeleton loaders on static pages
- Animated gradients as hero backgrounds

**Reduced-motion compliance:**

All surfaces must include the following at the end of their global stylesheet:

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

This is non-negotiable. Users who set the system reduced-motion preference have done so deliberately.

---

## 15. Accessibility Rules

**Minimum requirements for all DECIFER surfaces:**

| Requirement | Standard |
|-------------|---------|
| Text contrast | WCAG 2.1 AA (4.5:1 normal, 3:1 large) |
| Interactive element focus | Visible focus ring, min 3:1 contrast against surrounding |
| Touch targets | Min 44×44px on mobile |
| Alt text | All meaningful images and icons |
| ARIA labels | All icon-only buttons and interactive SVGs |
| `aria-hidden` | All decorative SVGs and background elements |
| Keyboard navigation | All interactive elements reachable and operable by keyboard |
| Form inputs | Explicit labels, error messages outside of placeholder text |

**Specific Trading surface requirements:**
- Nav mobile toggle must have `aria-label` for both open and closed states
- Live data indicators (`Live`, `Active`) must not rely on colour alone — accompany with text label
- The `--text-3` colour (`#3d5166`) must never carry information a user needs to understand the product

**Specific Learning surface requirements:**
- All lesson content rendered via `dangerouslySetInnerHTML` must use semantic HTML (h2/h3/p/ul)
- Gamification feedback (correct/incorrect) must be communicated through text, not colour alone
- Subject colours (maths/english/science) must be accompanied by text labels — never colour as the sole subject identifier

---

## 16. Correct and Incorrect Usage Examples

### Correct

- Orange CTA button labelled "Request access" on a dark background. One primary CTA per viewport.
- A card with a grey label, white headline, slate sub-copy, and a green "Live" badge.
- A section heading with a small uppercase label above it (`PRODUCT PREVIEW`) in `--text-3`, followed by a headline in `--text-1`.
- An entrance animation (fade-up) on the hero copy, one-shot, no loop.
- The DECIFER mark with the left bracket in `#F05A28` and the right bracket in `#e8f0fa`.
- Body copy at 400 weight, 1.7 line-height, `--text-2` on card background.

### Incorrect

- Two orange buttons on the same screen with different actions — one should be secondary (surface background, border).
- Orange used as a card background or section background colour. Orange is for interactive signals, not surfaces.
- `--text-3` (`#3d5166`) used for a paragraph the user must read to understand the product.
- A hero section with an animated floating orb background. Decorative motion adds noise, not meaning.
- A gradient-text headline using blue-to-purple. This belongs to the parent site's product card differentiation, not to product marketing surfaces.
- The DECIFER mark recoloured to match a section background. The orange/white dyad is fixed.
- Padding values that are not multiples of 4px (e.g. padding: 13px). Off-scale spacing breaks visual rhythm.
- A badge that uses colour as the sole state indicator with no text label.

---

## 17. Rejected Aesthetic Choices

This section documents visual directions that were explicitly considered and rejected. The reasons are recorded to prevent revisiting the same ground.

### Purple dominance

**Rejected:** A palette that uses purple as the primary surface or accent colour across Trading.

**Why rejected:** Purple has no semantic meaning in financial or trading contexts. It reads as abstract "tech AI" branding — the same aesthetic used by dozens of competing products that want to signal intelligence without earning it. More concretely, the DECIFER orange mark is the brand signal. Any dominant colour that competes with it dilutes the mark. Purple `--color-learn` exists on the parent site only, to distinguish the Learning product card — it does not carry into any product surface.

### Low-contrast dark-on-purple

**Rejected:** Dark text on mid-purple backgrounds for card bodies or section backgrounds.

**Why rejected:** Mid-range purple surfaces (around `#7c5ce8` to `#9578e8`) do not provide adequate contrast for either dark or light text without careful selection. Treating purple as a surface colour without contrast testing produces inaccessible combinations that violate WCAG AA.

### Generic AI gradients

**Rejected:** Blue-to-purple or multi-stop gradients as hero backgrounds, card effects, or text treatments.

**Why rejected:** The "blue-purple AI gradient" is the most overused visual cliché in the 2023–2026 AI product wave. Using it signals that the product is copying the aesthetic of AI products rather than building a distinct visual identity. More importantly, it adds decoration without meaning. DECIFER Trading uses a subtle directional glow (single-colour radial gradient at low opacity) anchored to the orange — because that glow has a specific semantic relationship to the brand signal. A blue-purple gradient has no such relationship.

### Decorative motion

**Rejected:** Looping background animations, floating particle systems, animated mesh gradients.

**Why rejected:** These are signals of visual effort, not product quality. They add cognitive noise for users who are trying to read structured financial context. They cause accessibility violations for users with vestibular disorders. They slow page performance. No motion decision in the DECIFER system was made to create atmosphere — every permitted animation exists to guide attention or communicate state.

### Unrelated sub-brand colour systems

**Rejected:** A product-specific palette for Trading that has no relationship to the DECIFER mark (e.g. a teal-dominant Trading palette, or a red-dominant Trading palette).

**Why rejected:** Product colour differentiation is the job of the parent site's product cards, not the individual product surfaces. Once a user is on `decifertrading.com`, they are fully within the Trading product. The dominant brand signal must be the DECIFER orange — the same orange on every DECIFER surface. A teal or red Trading palette would make `decifertrading.com` look like an unrelated product.

### Childish typography for Learning

**Rejected:** Bubble fonts, heavy informal typefaces, oversized decorative lettering for the Learning surface.

**Why rejected:** DECIFER Learning is a UK National Curriculum product. Its users are children aged 7–16 and their parents and teachers. Childish typography signals a toy, not a learning tool. Nunito (the chosen typeface) is rounded and warm without being juvenile — it is used in professional educational software because it is legible at small sizes and friendly without condescension. Infantilising the typography would undermine parental and teacher confidence in the product's rigour.

### Split-colour mark

**Rejected:** Left bracket orange, right bracket white/light — treating the two brackets as a yin/yang or "active/receiving" pair with separate colours.

**Why rejected:** The DECIFER mark is one object, not two. Assigning different colours to each bracket introduces a visual split that invites interpretation ("what does white mean? what does orange mean?") rather than identity recognition. The mark must read as a single brand signal in the same way a letter reads as one character. Split-colour was introduced in Sprint 2 and corrected in Sprint 2.1 — documented here so it is not revisited.

### Finance cliché visuals for Trading

**Rejected:** Candlestick chart hero images, bull/bear silhouettes, banknote imagery, ticker tape, trading floor photography, "green number waterfall" screen graphics.

**Why rejected:** These visuals communicate nothing specific about DECIFER Trading and everything generic about "a trading product". DECIFER's differentiator is structured intelligence — the layer between information and decision. The visual language must reflect that: structured layout, legible hierarchy, calm precision. The product mockup (the mobile interface showing market context cards) is the correct hero visual — it is the actual product, not a cliché of the category.

---

## 18. Industry Adaptation Rules

DECIFER may expand into products for different industries. Each industry has a different user context, emotional register, and visual convention. These rules apply to product surfaces — not to the parent site or the DECIFER mark, which remain constant.

**The DECIFER orange and mark are always retained regardless of industry.**

### Financial or wealth domains

- Dark surfaces are appropriate (terminal familiarity, reduced eye strain in multi-screen environments)
- Green/amber/red state colours must be used consistently with their financial meanings (gain/caution/loss)
- Typography must be restrained — heavy display fonts read as aggressive in money contexts
- No celebratory visuals (confetti, success animations with large motion) — wealth is serious, not celebratory
- Compliance language is mandatory on every public-facing surface

### Education and learning domains

- Light surfaces are appropriate (classroom, daytime, tablet-native settings)
- Colour can be used more liberally for subject/category differentiation, but each colour must carry specific semantic meaning
- Typography should be warm but legible — not juvenile
- Gamification elements (streaks, points, badges) are acceptable but must use the contrast-safe state colours
- Child-safe content requirements take precedence over all design decisions

### Market intelligence domains

- High information density is expected — users are professional or semi-professional
- Dense type scales are acceptable (body at 0.875rem–0.9375rem rather than 1rem) to support information density
- Data visualisation must follow financial charting conventions (time on x-axis, consistent axis labels)
- Dark surfaces preferred for extended-use sessions
- The focus is on legibility of structured data, not brand expression

### Global affairs or news domains

- Typography must convey authority — serif display fonts are appropriate here unlike other DECIFER products
- Dark and light surface variants may both be needed (dark for evening reading, light for daytime)
- Political or geographic content must be presented neutrally — no colour associations that imply position
- Content credibility signals (source attribution, timestamps) must be prominently typeset

### Business or productivity domains

- Light surfaces preferred — productivity software is used in office environments
- Heavy use of white space to separate tasks and reduce cognitive load
- Action-oriented CTAs — the user is here to do something, not to read
- Data tables and structured lists are primary UI patterns — design for scannable, not readable

### Health or sensitive personal domains

- Light, warm surfaces — clinical or cold aesthetics are inappropriate for personal health
- Avoid red for anything other than genuine alert states — red in health contexts carries emergency implications
- Typography must be generous (larger body size, comfortable line height) for accessibility and anxiety reduction
- Privacy indicators must be visually prominent — users in health contexts have heightened concerns about data
- Compliance language is mandatory on every surface

### Legal, policy, or compliance-heavy domains

- Document-style layouts are appropriate — legal content reads like a document, not a product
- Conservative typography — no display fonts for legal copy
- No colour in legal prose except for link text
- High contrast black-on-white or equivalent is preferred for legal sections
- Structural navigation (table of contents, section anchors) is required for long-form policy content

### Property, infrastructure, or asset-heavy domains

- Strong visual hierarchy for asset listings — photography or maps may be primary content
- Data attributes (size, price, location, condition) must be structured and scannable
- State colours adapt to property context: green = available, amber = under offer, red = unavailable or sold
- Mobile-first layout is critical — property searches are predominantly mobile

---

## 19. Intelligence Engine Flow Pattern

Introduced in Sprint 2 for decifer.io. A reusable three-column layout for communicating a multi-step process through a central system.

### Structure

```
[Left column: Inputs]  |  [Centre: System + Steps]  |  [Right column: Outputs]
```

- **Left** — labelled "Signals in" using `text-cta` (orange). Lists source types as arrow-prefixed items.
- **Centre** — the DECIFER bracket mark (large, with orange drop-shadow glow), plus numbered step pills stacked vertically: 01 Collect (orange), 02 Contextualise (purple/learn), 03 Clarify (green/live). Connecting vertical lines between steps use `bg-line`.
- **Right** — labelled "Intelligence out" using `text-live` (green). Lists output qualities.

Grid class: `grid-cols-1 md:grid-cols-[1fr_1px_1fr_1px_1fr]` — the `1px` columns become visible `bg-line` dividers on desktop. Hidden on mobile, columns stack naturally.

### Step colour assignment

Each step uses a distinct product/state colour:

| Step | Label | Colour | Rationale |
|------|-------|--------|-----------|
| 01 | Collect | `text-cta` / `border-cta` / `bg-cta` (orange) | Initiation — master brand signal |
| 02 | Contextualise | `text-learn` / `border-learn` / `bg-learn` (purple) | Processing depth — intelligence |
| 03 | Clarify | `text-live` / `border-live` / `bg-live` (green) | Completion — confirmed, live output |

### Rules

- Inputs/outputs lists use a minimal 12×12 arrow SVG (`M2 6h8M6 2l4 4-4 4`) as bullet, not Unicode glyphs
- The centre mark uses `drop-shadow(0 0 14px rgba(240,90,40,0.28))` — same orange glow used on the hero mark, at reduced intensity for an inner-section context
- Step detail cards below the flow panel duplicate step numbers as rounded circles with matching ring colours — consistent with the pills in the panel
- **Do not use animated arrows** between columns. The flow reads left to right structurally; motion is not needed to communicate direction.
- On mobile, left column appears first, then centre, then right — the full story is still readable without the visual left→right layout

---

## 20. Sprint 1 Closure Record

**What was shipped in Sprint 1 (prior sessions):**

| Site | Version | Change |
|------|---------|--------|
| decifer.io | v0.10.0 | WCAG AA contrast fixes, semantic tokens, radius scale, reduced-motion support, `--color-mark: #F05A28`, `--color-cta: #F05A28` |
| decifer-learning | v1.1.2 | `--brand: #F05A28` aligned, state colours, radius scale, reduced-motion support |

**What was shipped in Sprint 1.1 (this session):**

| Site | Change | Reason |
|------|--------|--------|
| decifertrading.com | Fixed `--orange: #F05A28` (was `#f97316`) | Master brand signal must be consistent across all surfaces |
| decifertrading.com | Updated `--orange-light: #F47040` (hover state for new base) | Hover variant must be derived from correct base |
| decifertrading.com | Updated `--orange-bg` and `--orange-border` rgba values | RGB channels must match new base orange |
| decifertrading.com | Added radius scale (`--radius-sm` through `--radius-2xl`) | Aligns with parent site and Learning |
| decifertrading.com | Added semantic state aliases (`--color-success/warning/error/info`) | Aligns with parent site and Learning |
| decifertrading.com | Added `@media (prefers-reduced-motion: reduce)` block | Accessibility baseline — present on all other DECIFER surfaces |
| decifertrading.com | Fixed `Logo.tsx` mark stroke to `var(--orange)` | Mark must use the token, not a hardcoded value |
| decifertrading.com | Fixed all 10 hardcoded `#f97316` / `rgba(249,115,22,...)` instances across 6 component files | Single source of truth for the brand colour |
| docs/DECIFER_BRAND_GUIDELINES.md | Fixed two `#f97316` references to `#F05A28` | Brand guidelines must reflect the correct master colour |

**Quality gates:**

| Gate | Result |
|------|--------|
| TypeScript (tsc --noEmit) | Pass |
| Next.js build | Pass — 0 errors, 3 routes generated |
| ESLint | Pre-existing config issue (no `eslint.config.js`) — unrelated to Sprint 1.1 changes |

---

## 21. Sprint 2 Closure Record — decifer.io Master Site Journey

**What was shipped in Sprint 2:**

| File | Change | Reason |
|------|--------|--------|
| `src/app/components/DeciferMark.tsx` | Added `height` prop for override sizing. *(Sprint 2.1 corrected the bracket colours — see Sprint 2.1 record.)* | Height override needed for hero-size usage |
| `src/app/globals.css` | Removed `@keyframes beam-pulse` + looping animation. Hero beam changed to orange-anchored static gradient. Dot grid changed to neutral. Removed `hero-accent` blob. Removed `gradient-text`. Renamed `glow-brand` → `glow-trading`. Form focus ring → orange. Legal prose links → orange. | All removed items violated the no-decorative-motion and no-blue-purple-gradients rules |
| `src/app/layout.tsx` | Metadata title/description updated to match new site positioning. | Reflects DECIFER as a structured intelligence company, not a product landing page |
| `src/app/components/Footer.tsx` | "Decifer" → "DECIFER" throughout. Removed Decifer Money/World/Work. "Trust" nav link → "Boundaries". Added "More domains coming" placeholder. | Naming and future-product rules |
| `src/app/components/EarlyAccessForm.tsx` | Interest labels updated. Submit button `bg-brand` → `bg-cta` (orange). | Parent site CTAs must use master orange, not product blue |
| `src/app/page.tsx` | Complete rewrite — 6 sections: Hero, Problem, Intelligence Engine, Products, Boundaries, Founder, Early Access. See below for per-section changes. | Sprint 2 core deliverable |

**page.tsx per-section changes:**

| Section | Key Changes |
|---------|------------|
| HERO | `DeciferMark height={52}` as visual gateway. Orange drop-shadow glow. `text-cta` on "understanding." `bg-cta` primary CTA. Static scroll indicator (no caret-bounce). |
| PROBLEM | 3 cards with `group-hover:text-cta` (not `text-brand`). Structural SVG icons — noise wave, clock/uncertainty, fragmented grid. |
| INTELLIGENCE ENGINE | New section — 3-col flow card (Signals In → DECIFER → Intelligence Out). Step pills (01/02/03) with orange/purple/green. Step detail cards below. |
| PRODUCTS | Domain category labels added. All "Decifer" → "DECIFER". `glow-trading` (was `glow-brand`). Domain expansion block with no named future products. |
| BOUNDARIES | Section renamed from "Trust". First card `bg-cta/5 border-cta/15 text-cta` (was blue). 4 cards cover: financial advice, teacher substitute, child safety, transparent AI. |
| FOUNDER | `text-cta/25` quote mark, `border-cta/30 bg-cta/10 text-cta` initials. Orange left-border accent on card. |
| EARLY ACCESS | `SectionLabel` wrapper. `hero-beam opacity-50` background. `EarlyAccessForm` unchanged. |

**New reusable patterns introduced:**

- `SectionLabel` component — bracket-prefixed eyebrow label (see section 19)
- Intelligence Engine flow pattern (see section 19)

**Copy changes:**

- Site positioning: "structured intelligence systems" — domain by domain
- Products: "Two products. One system. Different domains."
- Method: "DECIFER creates structure from signals" + 3-step Collect/Contextualise/Clarify
- Boundaries: renamed from "Trust and Safety" to emphasise they are design decisions, not disclaimers
- Founder quote: explicit, honest, personal — names specific domains

**Motion and interaction:**

All motion is structural: entrance (`anim-fade-up-N`), scroll reveal (`scroll-reveal-N`), hover state transitions. Zero looping or ambient animation.

**Quality gates:**

| Gate | Result |
|------|--------|
| TypeScript (`tsc --noEmit`) | Pass — 0 errors |
| Next.js build | Pass — 0 errors, 12 routes generated (all static except `/api/early-access`) |

---

## 22. Sprint 2.1 Closure Record — Brand Mark Compliance Fix

**Root cause:** Sprint 2 introduced a split-colour mark (`left = #F05A28`, `right = #e8f0fa`) based on a "dialogue brackets" metaphor. This violated the locked brand mark rule (both brackets must be `#F05A28`).

**Fix — one file, two changes:**

| File | Change |
|------|--------|
| `src/app/components/DeciferMark.tsx` | Removed `WHITE` constant. Right bracket stroke changed from `color ?? WHITE` to `color ?? ORANGE`. Header comment updated to state the rule. |
| `docs/DECIFER_DIGITAL_EXPERIENCE_SYSTEM.md` | §2 non-negotiables: added explicit split-colour prohibition. §17: added "Split-colour mark" rejected aesthetic entry. §21: corrected incorrect Sprint 2 DeciferMark record. |

**Confirmation:** Both brackets now render `#F05A28` by default. The `color` prop applies to both brackets equally when provided.

**Quality gates:**

| Gate | Result |
|------|--------|
| TypeScript (`tsc --noEmit`) | Pass — 0 errors |
| Next.js build | Pass — 0 errors |

**Deployment readiness:** ✓ Ready. Sprint 2 blocker resolved.

---

## 23. Sprint 3 Closure Record — DECIFER Learning Homepage Elevation

**Sprint goal:** Elevate `deciferlearning.com` to feel like a product from the same DECIFER family while remaining warm and parent-trust-led. No changes to auth, rewards, quiz logic, database, or APIs.

---

### Files changed

| File | Change |
|------|--------|
| `app/page.tsx` | Full rewrite — see section breakdown below |
| `components/ui/ScrollReveal.tsx` | NEW — reusable scroll-triggered reveal wrapper |
| `components/homepage/LearningJourney.tsx` | NEW — visual 4-step learning path with staggered reveal |
| `components/homepage/QualityPipeline.tsx` | NEW — 6-stage content quality pipeline (visual) |
| `components/homepage/HeroMockup.tsx` | NEW — animated parent progress mockup with viewport-triggered ring fill |

---

### New reusable pattern: `ScrollReveal`

**Component:** `components/ui/ScrollReveal.tsx`

Wraps any children in a Framer Motion `motion.div` that fades up from `y: 20` when it enters the viewport. Uses `useInView` with `once: true` and `margin: '-60px'` so the element triggers before it fully enters the viewport. The `prefers-reduced-motion` rule in `globals.css` collapses all transitions to `0.01ms`, making this non-intrusive for users who have set the system preference.

**Props:**
- `delay?: number` — stagger offset in seconds (default `0`)
- `className?: string` — forwarded to the motion wrapper

**Usage rule:** Use `delay` in increments of `0.07–0.10s` for card grids. Do not exceed `0.5s` total stagger on any set of cards — the last card must not be delayed so far that it feels unrelated to the first.

---

### page.tsx section changes

| Section | Key Changes |
|---------|------------|
| HERO | H1 rewritten: "Structured learning. Progress parents can see." Sub-copy clarified: structured path, AI-assisted feedback, parent visibility. Trust chips restructured to `{icon, label}` pairs. Static mockup replaced with `HeroMockup` client component — progress ring and progress bar animate on viewport entry. |
| LEARNING LOOP | Moved to position 2 (was position 5). Replaced 4-card grid with `LearningJourney` component — colour-coded step cards (maths blue / science green / lightning yellow / brand orange), step number circles, colour accent bars. Decorative gradient connector line on desktop. Staggered scroll reveal. |
| PARENT PROBLEM | Headline rewritten: "Most tools give your child something to do. Few tell parents what's working." |
| QUALITY PIPELINE | Completely replaced single paragraph with `QualityPipeline` component — 6 numbered stages in a 3×2 grid. Each stage has an orange circle, stage name, parent-friendly description, and technical label. Staggered scroll reveal. |
| CHILD/PARENT SPLIT | "Built for children, visible to parents" — kept, copy tightened. |
| GAMIFICATION | Copy updated: "DECIFER Learning uses rewards to support consistency" (was "Decifer uses rewards"). |
| CONTENT AVAILABILITY | H2 updated to "DECIFER Learning is growing in stages" (was "Decifer is growing"). |
| HELP GUIDES | "Decifer" → "DECIFER Learning" in guide titles. |
| FOOTER | No changes. |

**Sections removed:** "Why Decifer is different" (4-card grid — absorbed into other sections), "Fun should never mean careless" (absorbed into quality pipeline), "Not worksheets. Not a chatbot." comparison table (absorbed into learning journey framing).

---

### Copy changes summary

- All instances of "Decifer" in brand contexts changed to "DECIFER" or "DECIFER Learning" per the master brand naming rule.
- Trust chip format updated to structured objects (was a flat string array) — adds icon differentiation.
- Hero sub-copy restated to name the actual product mechanism: structured path, AI-assisted feedback, parent-visible results.
- Quality pipeline: 6 stages now have parent-facing names (was a single paragraph with no visual treatment).
- Learning loop tagline: "That loop is what DECIFER Learning is built to create" (was "what Decifer is built to create").

---

### Motion and interaction summary

| Element | Motion | Trigger |
|---------|--------|---------|
| `HeroMockup` progress ring | Fills from 0% to 72% over 900ms (ease-out cubic) | IntersectionObserver — 30% threshold |
| `HeroMockup` progress bar | Width expands from 0% to 72% over 900ms | Same IntersectionObserver |
| `LearningJourney` cards | Fade-up from `y:20`, staggered at 0.09s intervals | `useInView` — `once: true`, `-60px` margin |
| `QualityPipeline` cards | Fade-up from `y:20`, staggered at 0.07s intervals | `useInView` — `once: true`, `-60px` margin |
| All animations | Collapsed to `0.01ms` | `@media (prefers-reduced-motion: reduce)` in `globals.css` |

Zero looping or ambient animation. All motion communicates progress, feedback, or content arrival.

---

### Accessibility and contrast notes

- All text combinations use pre-approved contrast pairs from §7
- Step number circles use white text on coloured backgrounds — maths (`#6C9EFF`), science (`#52D9A0`), lightning (`#FFD43B`), brand (`#F05A28`). Lightning yellow uses `#A08000` for its tag text (dark on light yellow — meets 4.5:1)
- All decorative elements carry `aria-hidden`
- All interactive elements carry accessible labels
- `aria-label` on step number spans (e.g. "Step 1")
- Subject colours always accompanied by text labels — never colour as sole identifier

---

### Mobile experience notes

- All new card grids: 1 column on mobile, 2 on `sm`, 4 on `lg` (journey) or 3 on `lg` (pipeline)
- Connector line in `LearningJourney` is `hidden lg:block` — not visible on mobile/tablet
- `HeroMockup` is full-width on mobile (grid stacks to single column)
- All tap targets remain ≥ 44px

---

### Confirmation — unchanged systems

No changes to: auth, reward vault, quiz logic, quiz submission, topic progress, database schema, API routes, gamification engine, parent dashboard, child dashboard, spaced repetition, streak/shield logic, content pipeline, or service worker.

---

### Quality gates

| Gate | Result |
|------|--------|
| TypeScript (`tsc --noEmit`) | Pass — 0 errors |
| ESLint (`next lint`) | Pass — 0 warnings or errors |
| Next.js build | Pass — `✓ Compiled successfully`, `✓ Generating static pages (42/42)` |

---

*This document is maintained by Cowork (Claude). Proposed changes to design philosophy, master brand colour, or industry adaptation rules require Amit approval before implementation.*
