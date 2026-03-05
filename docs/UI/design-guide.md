# Odin UI Design Guide (Phase 0 Baseline)

Last updated: 2026-03-01

## 1) Purpose
Define a stable visual and layout system before feature growth so all future UI work is consistent, predictable, and easy to evolve.

Primary UX goal:
- A chart-first trading workspace with dedicated spaces for tools and widgets.

## 2) Template Direction
We evaluated two free options:
- Tailwind Admin template: strong dashboard scaffolding, but heavier style opinion and more adaptation overhead for our focused chart workspace.
- shadcn/ui: clean visual language, composable primitives, and easier long-term extension.

**Decision:** use a **shadcn-style design foundation** (clean cards, subtle borders, neutral dark palette, predictable spacing) adapted to the current React/Vite setup.

Notes:
- This baseline does not require importing the full shadcn component library yet.
- If needed later, we can formalize by adding Tailwind + shadcn CLI in a dedicated migration step.

## 3) Layout Contract
Viewport is organized into 4 persistent zones:
1. Header (top tools rail)
2. Main workspace (left panel + chart center + right panel)
3. Footer (minimal status rail)

### 3.1 Header
- Fixed region at top of app.
- Contains product identity, selected symbol, connection status, and reserved tool slots.
- Compact height target: ~56px.

### 3.2 Main Workspace
- **Center chart region is dominant** and always receives most width.
- **Left panel**: watchlist and quick symbol search, collapsible.
- **Right panel**: overlay agents widget + bots placeholder, collapsible.
- Default desktop state: **both panels expanded**.

Recommended desktop widths:
- Left panel expanded: ~260px
- Right panel expanded: ~320px
- Collapsed rail width: ~44px

### 3.3 Footer
- Minimal footer only (thin status rail).
- Shows compact runtime/system hints and future extension points.

## 4) Theme and Visual Language
Default theme is **dark**.

Current baseline palette direction:
- Near-black / neutral grayscale surfaces (not blue-forward).
- Color accents reserved for status semantics (healthy/warning/error) and chart up/down candles.

### 4.1 Color System (semantic)
Use CSS variables for all theme values.

Core tokens:
- `--bg`: app background
- `--surface-1`: header/footer/panel base
- `--surface-2`: cards and grouped controls
- `--surface-3`: hover/active surfaces
- `--border`: standard border color
- `--text-primary`: main text
- `--text-muted`: secondary text
- `--accent`: primary accent
- `--success`: positive status
- `--danger`: error/disconnect status

Chart-specific tokens:
- `--chart-bg`
- `--chart-grid`
- `--chart-text`
- `--chart-up`
- `--chart-down`

### 4.2 Styling Principles
- High contrast where data is critical; low visual noise elsewhere.
- Subtle radius and borders for structure, not heavy shadows.
- Consistent spacing cadence (8px base scale).
- Keep animation lightweight (fast transitions for hover/collapse only).

## 5) Interaction Rules

### 5.1 Panel Collapse
- Each side panel has a collapse/expand control in its header.
- On collapse, panel converts to a thin vertical rail with icon-only affordance.
- Chart expands to consume released space.
- Persisting panel state can be added later (local storage).

### 5.2 Widget Areas
- Left and right panels are both **widget areas**.
- Widgets can be reordered via drag-and-drop.
- Widgets can be moved between left and right areas.
- Widgets support vertical resizing via draggable bottom resize handles.

### 5.3 Watchlist (Widget)
V1 includes:
- Quick symbol search input
- Watchlist items: SPY, QQQ, IWM

Behavior:
- Clicking a symbol sets active symbol in UI state.
- Chart title and context labels update with selected symbol.
- Data stream symbol switching can be wired later without layout changes.

### 5.4 Overlay Agents (Widget)
V1 includes:
- Agent rows with name and heartbeat indicator
- Status levels: healthy / delayed / offline

### 5.5 Bots Placeholder (Widget)
V1 includes:
- A placeholder card for active trading bots and future controls.

## 6) Component Structure Guidelines
- Keep layout primitives generic and reusable:
  - `AppShell`
  - `TopBar`
  - `SidePanel`
  - `WorkspaceFooter`
  - `WidgetCard`
- Keep chart integration isolated in `ChartView`.
- Avoid embedding long inline styles; centralize styling in CSS.

## 7) Accessibility Baseline
- Interactive controls must be keyboard-focusable.
- Provide visible focus styles on dark surfaces.
- Ensure status indicators are not color-only (text label included).
- Maintain readable contrast in all panel/card text.

## 8) Responsive Baseline
- Desktop-first workspace target.
- On narrower widths, side panels should still collapse cleanly before center chart loses usability.
- Mobile-specific redesign is out of scope for this phase.

## 9) Near-Term Evolution
Planned next steps after this baseline:
1. Add state persistence for panel open/closed and selected symbol.
2. Add configurable widgets in left/right panel stacks.
3. Add header tool actions (timeframe, overlays, layout presets).
4. Introduce full shadcn component library if we need richer form/dialog primitives.

## 10) Non-Goals (for now)
- No full design token pipeline.
- No theming switcher (dark is default and only mode for now).
- No heavy animation system.
- No complete mobile app layout redesign.
