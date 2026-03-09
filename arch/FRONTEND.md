# Frontend Architecture

## Tech Stack

| Technology              | Purpose                                    |
|-------------------------|--------------------------------------------|
| React 18                | UI framework                               |
| TypeScript              | Type safety                                |
| Vite 5                  | Build tool and dev server                  |
| React Flow 11           | Interactive graph/node canvas              |
| React Router v7         | Client-side routing                        |
| Clerk (`@clerk/clerk-react`) | Authentication (sign in/up, JWT)      |
| react-syntax-highlighter | Syntax highlighting (Prism + oneDark)     |
| react-markdown          | Markdown rendering for AI output           |
| Framer Motion           | UI animations                              |
| lucide-react            | Icon library                               |
| axios                   | HTTP client                                |
| dagre                   | Graph layout (dependency of layoutUtils)   |
| Tailwind CSS 3          | Utility CSS framework (in devDependencies) |

---

## File Structure

```
frontend/src/
├── main.tsx                     # Entry point, routing, Clerk provider
├── App.tsx                      # UNUSED — legacy wrapper, not imported
├── CodeMap.tsx                  # Main IDE component (~1114 lines)
├── CodeMap.css                  # Full IDE styling (dark theme)
├── index.css                    # Tailwind imports / global styles
├── types/
│   └── index.ts                 # Shared TS interfaces (RN, RE, MemberData, ClusterMetadata)
├── config/
│   └── constants.ts             # API_BASE, WS_BASE, layout constants, ENTRY_KW
├── context/
│   └── TourContext.tsx           # Narrator tour state provider + useTour hook
├── pages/
│   ├── AppLayout.tsx            # App shell with header nav + Outlet
│   ├── LandingPage.tsx          # Public auth landing page
│   ├── ProtectedRoute.tsx       # Clerk auth guard
│   ├── MyGraphsPage.tsx         # Saved program graphs browser
│   └── ConversationPage.tsx     # WebSocket chat UI
├── components/
│   ├── AuthRequestInterceptor.tsx # Clerk JWT injection for axios
│   ├── context.ts               # FocusCtx + MemberClickCtx React contexts
│   ├── EzNode.tsx               # Custom React Flow node (function/class)
│   ├── EzEdge.tsx               # Custom React Flow edge (animated bezier)
│   ├── FileGroupNode.tsx        # Architect-view file group node
│   ├── FileItem.tsx             # File explorer sidebar item (recursive)
│   └── LibraryNode.tsx          # 3rd-party dependency node (circular)
└── lib/
    └── layoutUtils.ts           # Graph layout algorithms (treeLayout, applyEdgeFocus)
```

---

## Routing (`main.tsx`)

```
/                  → LandingPage (public, Clerk auth)
/app               → ProtectedRoute → TourProvider → AppLayout shell
  /app             → EzDocsIDE (default index route — the IDE)
  /app/graphs      → MyGraphsPage (saved program graphs)
  /app/conversation → ConversationPage (WebSocket chat)
*                  → Navigate to / (catch-all redirect)
```

The Clerk `<ClerkProvider>` wraps the entire app. `<AuthRequestInterceptor>` is mounted globally (outside the router) to inject JWTs into all axios requests. Protected routes require sign-in via `<ProtectedRoute>`.

---

## Component Breakdown

### `LandingPage.tsx` — Authentication Gate

- **Signed out:** Shows product tagline ("Turn codebases into interactive dependency graphs...") with Sign In / Sign Up modal buttons.
- **Signed in:** Auto-redirects to `/app` via `useNavigate` in a `useEffect`.

### `AppLayout.tsx` — Application Shell

The persistent layout wrapper for all authenticated routes:

```
┌──────────────────────────────────────────────┐
│  [EzDocs logo]   IDE | My graphs | Conversation  👤 │  ← Header
├──────────────────────────────────────────────┤
│  [Tour in progress banner — if narrating]    │  ← Conditional
├──────────────────────────────────────────────┤
│                                              │
│              <Outlet />                      │  ← Child route content
│                                              │
└──────────────────────────────────────────────┘
```

- **Active tour banner:** When `isNarrating` is true (from `TourContext`) and user is NOT on `/app`, shows: "Tour in progress → [Go to IDE] [Stop tour]".
- Nav links: "IDE" → `/app`, "My graphs" → `/app/graphs`, "Conversation" → `/app/conversation`.
- Renders `<UserButton>` (Clerk) for account management.

### `ProtectedRoute.tsx` — Auth Guard

Wraps children in Clerk's `<SignedIn>`. If signed out, redirects to `/` with `state: { from: location }` for post-login redirect.

### `CodeMap.tsx` — The IDE (Core Component)

The main component of the entire application (~1114 lines). Exported as `EzDocsIDE`, wrapped in `<ReactFlowProvider>`.

#### 1. Analysis Input Panel

Three modes for loading a codebase:
- **Local path** — Text input with "Analyze" button
- **GitHub URL** — Text input, clones and analyzes
- **ZIP upload** — File picker, uploads and analyzes

#### 2. Graph Visualization (React Flow Canvas)

Custom node types registered with React Flow:

| Node Type    | Component       | Description                                |
|--------------|-----------------|--------------------------------------------|
| `ez`         | `EzNode`        | Standard code symbol (function/class)      |
| `fileGroup`  | `FileGroupNode` | Architect view — file-level grouping       |

Custom edge type: `ez` (`EzEdge`) — animated cubic bezier edges with focus-aware styling.

**Note:** `LibraryNode` is defined (`components/LibraryNode.tsx`) but is not currently registered in the `NODE_TYPES` map.

#### 3. Focus System

When a node is clicked, the graph enters focus mode:
- **Focused node** — Bright accent border + glow (`ez-focused`)
- **Callers** — Highlighted with caller color (`ez-caller`)
- **Callees** — Highlighted with callee color (`ez-called`)
- **Unrelated nodes** — Dimmed (reduced opacity, `ez-dim`)
- Applied via `applyFocusRef` function reference for minimal re-renders.
- Focus state propagated through `FocusCtx` context.

#### 4. Sidebar

Left sidebar with file explorer:
- Recursive file tree using `FileItem` component.
- Indentation based on depth.

#### 5. Detail Panel

Right panel shown when a node is selected:
- Node name, type, file path
- Source code with syntax highlighting (`react-syntax-highlighter` + Prism oneDark)
- AI-generated explanation/summary
- "Narrate this node" button for deep-dive

#### 6. Codebase Tour (AI Narrator)

Connects to the `/ws/narrate` WebSocket:
- Streams markdown narration chunks from the narrator.
- Renders as formatted markdown via `react-markdown` in a tour panel.
- Supports interactive controls: Continue, Ask a Question, Jump to Node.
- **TTS integration:** Sentences are queued and spoken via browser `SpeechSynthesis` and/or Kokoro `AudioContext`.
- Narrator state managed globally via `TourContext`.

#### 7. Per-Node Narration

Connects to `/ws/narrate/node` WebSocket:
- Deep-dive explanation of a single node.
- Independent TTS pipeline from the main tour.

#### 8. In-IDE Chat

Connects to `/ws/chat` WebSocket:
- Streaming responses with `\x01` end-of-stream marker.
- Messages rendered with `react-markdown`.

#### 9. Saved Analyses

- Lists previously analyzed codebases from Postgres via `GET /analyses`.
- Load any saved analysis into the graph view.

#### 10. Custom Zoom Controls

`CustomZoomControls` component provides a zoom slider panel using `useReactFlow()` and `useViewport()`.

---

### `ConversationPage.tsx` — Standalone Chat

Full standalone chat interface (separate from the in-IDE chat):
- Single long-lived WebSocket to `${WS_BASE}/ws/chat`.
- Streaming protocol: text chunks from server, `\x01` byte signals end-of-stream.
- Messages rendered with `react-markdown`.
- Shows connection status ("Connected"/"Disconnected").

### `MyGraphsPage.tsx` — Program Graphs Browser

- Fetches saved program graphs from `GET ${API_BASE}/program/list` with Clerk JWT.
- Each graph item links to `/app` with `state: { programId }` via React Router state.
- Handles loading, error, and empty-list states.

---

### Custom React Flow Components

#### `EzNode.tsx` — Code Symbol Node

Custom React Flow node displaying a function or class:
- Reads `data`: `isFocused`, `isCalled`, `isCaller`, `isDim`, `isEntry`, `is_root_file`, `is_root_dep`, `_expandPulse`, `clusterColor`, `hasHidden`, `type`, `label`, `filepath`.
- Shows badges: ROOT, ENTRY, ROOT DEP, FOCUS, CALLS, CALLER.
- Green "+" badge when `hasHidden` is true (collapsed children).
- Invisible `<Handle>` elements on left (target) and right (source).

#### `EzEdge.tsx` — Animated Edge

Custom React Flow edge rendering a cubic bezier curve:
- Reads `data`: `line`, `inTree`, `_color`, `_sw` (stroke width), `_so` (stroke opacity), `_active`, `_expandPulse`.
- Active edges get a dashed animated flow overlay.
- Shows line-number label (`L{line}`) at midpoint when `inTree` is true.
- `_expandPulse` triggers a purple pulse animation.

#### `FileGroupNode.tsx` — File Group (Architect View)

Groups multiple code members under a single file node:
- Reads `data.members` as array of `{ id, name, type, code, ... }`.
- Each member row clickable via `MemberClickCtx`.
- Shows language color/label, ROOT/ENTRY/ROOT DEP badges.

#### `LibraryNode.tsx` — Third-Party Dependency (Not Wired)

Represents a third-party package as a 68x68px circular node:
- Shows `<Package>` icon, name, and "lib" badge.
- Not expandable, does not open detail panel.
- **Note:** Defined but not currently registered in `CodeMap.tsx` node types.

#### `FileItem.tsx` — File Explorer Item

Recursive collapsible file/folder tree item:
- Click toggles children for folders.
- Indentation: `depth * 14 + 6` px.
- Folders get chevron icons, files get `FileCode` icon.

---

## Layout Algorithms (`lib/layoutUtils.ts`)

### `treeLayout(rawNodes, rawEdges)`

Primary layout for the tree/node view:

```
Step 1: Separate library nodes from user-code nodes
Step 2: Group user nodes by file path
Step 3: Build file-level and node-level adjacency maps
Step 4: BFS from entry node → compute depth per file and per node
Step 5: Place nodes in cluster columns:
        X position = depth (dependency distance from entry)
        Y position = stacked within file cluster
Step 6: Library nodes → row below main graph
Step 7: Return { nodes, edges, clusters }
```

**Cluster coloring:** `getClusterColor(filepath, depth)` assigns deterministic HSL colors based on file path hash and depth. Uses a `BASE_COLORS` palette (blue, teal, amber, rose, purple, green).

### `applyEdgeFocus(edges, focusId)`

Updates edge visual states for focus mode:
- **Active edges** (connected to focused node) → bright color, thicker stroke.
- **Inactive edges** → dimmed, thinner.

### `langInfo(filename)`

Returns language metadata for file extension badges:

| Extension      | Label      | Color   |
|----------------|------------|---------|
| `.py`          | Python     | #3572A5 |
| `.js` / `.jsx` | JavaScript | #f1e05a |
| `.ts` / `.tsx` | TypeScript | #3178c6 |
| `.java`        | Java       | #b07219 |
| `.rs`          | Rust       | #dea584 |

### Layout Constants (`config/constants.ts`)

| Constant       | Value | Description                    |
|----------------|-------|--------------------------------|
| `LEVEL_W`      | 300   | Horizontal spacing per depth   |
| `LINE_SCALE`   | 2.2   | Vertical scale factor          |
| `NODE_W`       | 234   | Node width                     |
| `NODE_H`       | 80    | Node height                    |
| `NODE_GAP`     | 26    | Vertical gap between nodes     |
| `FILE_COL_W`   | 340   | File group column width        |
| `FILE_ROW_GAP` | 28    | Gap between file group rows    |
| `MEMBER_H`     | 22    | Height per member in file group|
| `HEADER_H`     | 88    | File group header height       |
| `FILE_NODE_W`  | 280   | File node width                |
| `ENTRY_KW`     | Set   | Entry-point keywords: main, index, app, run, start, init, setup, \_\_main\_\_, server, cli |

---

## Styling (`CodeMap.css`)

### Theme

Dark theme using CSS custom properties:

```css
:root {
  --bg-primary:    #0a0a0f;       /* Deep dark background */
  --bg-secondary:  #12121a;       /* Card backgrounds */
  --accent:        #6366f1;       /* Indigo accent */
  --text-primary:  #e2e8f0;       /* Light text */
  --font-mono:     'JetBrains Mono', monospace;
  --font-sans:     'Inter', sans-serif;
}
```

### Node Visual States

| State    | CSS Class       | Visual Treatment                       |
|----------|-----------------|----------------------------------------|
| Default  | `ez-node`       | Glass-morphism card with subtle border |
| Focused  | `ez-focused`    | Accent border + glow shadow            |
| Entry    | `ez-entry`      | Green accent bar on left side          |
| Caller   | `ez-caller`     | Teal highlight                         |
| Callee   | `ez-called`     | Amber highlight                        |
| Dimmed   | `ez-dim`        | Reduced opacity (0.3)                  |

### Animations

- `ez-spin` — Loading spinner rotation
- `ez-flow` — Edge flow animation (dashed line movement)
- `ez-edge-pulse-draw` — Edge drawing animation on focus
- `ez-node-expand-glow` — Node expansion glow effect
- `prefers-reduced-motion` — All animations disabled for accessibility

---

## State Management

The frontend uses **React local state** (no Redux/Zustand):

| State Category     | Storage             | Scope                          |
|--------------------|---------------------|--------------------------------|
| Graph data         | `useState` in CodeMap | Nodes, edges, clusters        |
| Master graph       | `useRef` (masterNodes/masterEdges) | Stable copy of all data |
| Focus state        | `useRef` + callback | Focus ID, applied via ref      |
| Tour/narration     | `TourContext`       | Global (shared via AppLayout)  |
| Auth               | Clerk               | Global (ClerkProvider)         |
| WebSocket          | `useRef`            | Per-connection in CodeMap      |
| TTS state          | `useRef`            | Sentence queue, AudioContext   |
| Chat history       | `useState`          | Per-session in ConversationPage|

### Why refs for focus?

The focus system uses `applyFocusRef` (a function stored in a ref) rather than state to avoid re-rendering the entire React Flow canvas when focus changes. This provides smooth, performant highlighting across hundreds of nodes.

---

## Contexts

### `TourContext` (`context/TourContext.tsx`)

Global narrator state shared across routes:
- `isNarrating: boolean` — Whether a tour is active.
- `setNarrating(v)` — Update narration state.
- `registerNarratorWs(ws)` — Store WebSocket ref for the active narrator.
- `stopNarration()` — Close WebSocket and set `isNarrating` to false.

### `FocusCtx` (`components/context.ts`)

Node focus state consumed by `EzNode`, `EzEdge`, `FileGroupNode`:
- `focusId: string | null`
- `outSet: Set<string>` — IDs of callees.
- `inSet: Set<string>` — IDs of callers.

### `MemberClickCtx` (`components/context.ts`)

Callback for clicking a member inside a `FileGroupNode`:
- `(m: MemberData) => void`

---

## Type Definitions (`types/index.ts`)

| Interface         | Fields                                              |
|-------------------|-----------------------------------------------------|
| `RN`              | `id: string`, `data: Record<string, any>`           |
| `RE`              | `id`, `source`, `target`, `label?`, `data?`         |
| `MemberData`      | `id`, `name`, `type`, `code`, `start_line`, `end_line`, `filepath` |
| `ClusterMetadata` | `id`, `filepath`, `bounds`, `color`, `isEntry`, `nodeCount` |
