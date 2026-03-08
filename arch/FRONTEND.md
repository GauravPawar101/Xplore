# Frontend Architecture

## Tech Stack

| Technology     | Purpose                                    |
|----------------|--------------------------------------------|
| React 18       | UI framework                               |
| TypeScript     | Type safety                                |
| Vite           | Build tool and dev server                  |
| React Flow     | Interactive graph/node canvas              |
| React Router   | Client-side routing                        |
| Clerk          | Authentication (sign in/up, JWT)           |
| Prism.js       | Syntax highlighting in code panels         |
| Web Speech API | Speech-to-text in chat                     |
| Kokoro TTS     | Text-to-speech for narrator (AudioContext) |

---

## File Structure

```
frontend/src/
├── main.tsx                    # Entry point, routing, Clerk provider
├── CodeMap.tsx                 # Main IDE component (~2000+ lines)
├── CodeMap.css                 # Full IDE styling (dark theme)
├── pages/
│   ├── AppLayout.tsx           # App shell with header + nav
│   └── LandingPage.tsx         # Public auth landing page
├── components/
│   └── LibraryNode.tsx         # Custom React Flow node for libraries
└── lib/
    └── layoutUtils.ts          # Graph layout algorithms
```

---

## Routing (`main.tsx`)

```
/                → LandingPage (public, Clerk auth)
/app             → AppLayout shell
  /app           → CodeMap (default child route, the IDE)
  /app/graphs    → (saved graphs listing)
  /app/chat      → (conversation view)
```

The Clerk `<ClerkProvider>` wraps the entire app. Protected routes require `<SignedIn>`.

---

## Component Breakdown

### `LandingPage.tsx` — Authentication Gate

- **Signed out:** Shows product tagline ("Turn codebases into interactive dependency graphs...") with Sign In / Sign Up modal buttons.
- **Signed in:** Auto-redirects to `/app`.
- Minimal centered layout with CSS variables.

### `AppLayout.tsx` — Application Shell

The persistent layout wrapper for all authenticated routes:

```
┌─────────────────────────────────────────────┐
│  [Xplore logo]   IDE | My Graphs | Chat  👤 │  ← Header
├─────────────────────────────────────────────┤
│                                             │
│              <Outlet />                     │  ← Child route content
│                                             │
└─────────────────────────────────────────────┘
```

- **Active tour banner:** When the AI narrator is running and the user navigates away from the IDE, a top banner appears: "Narrator is running → [Go to IDE] [Stop tour]".
- Uses `TourContext` to track narration state across routes.

### `CodeMap.tsx` — The IDE (Core Component)

This is the main component of the entire application (~2000+ lines). It handles:

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
| `library`    | `LibraryNode`   | Third-party package dependency (circle)    |

Custom edge type: `ez` (`EzEdge`) — animated edges with focus-aware styling.

**View modes:**
- **Tree view** (default) — Individual nodes laid out by dependency depth
- **Architect view** — Nodes grouped into file-level cards

#### 3. Focus System

When a node is clicked, the graph enters **focus mode**:
- **Focused node** — Bright accent border + glow
- **Callers** — Highlighted with caller color
- **Callees** — Highlighted with callee color
- **Unrelated nodes** — Dimmed (reduced opacity)
- Applied via `applyFocusRef` function reference for minimal re-renders

#### 4. Sidebar

Left sidebar with tabs:
- **File Explorer** — Recursive file tree for the analyzed directory
- **Search/Filter** — Filter nodes by name

#### 5. Detail Panel

Right panel shown when a node is selected:
- Node name, type, file path
- Source code with Prism.js syntax highlighting
- AI-generated explanation/summary
- "Narrate this node" button for deep-dive

#### 6. Codebase Tour (AI Narrator)

Connects to the `/ws/narrate` WebSocket:
- Streams markdown narration chunks from the LangGraph narrator
- Renders as formatted markdown in a tour panel
- Supports interactive controls: Continue, Ask a Question, Jump to Node
- **TTS integration:** Sentences are queued and spoken via browser `SpeechSynthesis` + Kokoro `AudioContext`

#### 7. Per-Node Narration

Connects to `/ws/narrate/node` WebSocket:
- Deep-dive explanation of a single node
- Shows callers/callees context
- Independent TTS pipeline from the main tour

#### 8. In-IDE Chat

Conversational chat with the codebase:
- WebSocket connection for real-time responses
- Session-based with message history persistence
- Speech-to-text input via Web Speech API
- RAG-powered: queries are answered using retrieved code context

#### 9. Saved Analyses

- List previously analyzed codebases from Postgres
- Load any saved analysis into the graph view
- Shows analysis metadata (name, date, node count)

#### 10. Progressive Expand

Starts with only entry-adjacent nodes visible to avoid overwhelming the user:
- Initial view shows entry points + their immediate callees
- "Expand" button reveals the next layer of dependencies
- Keeps the graph manageable for large codebases

---

### `LibraryNode.tsx` — Library Package Node

Custom React Flow node for third-party dependencies:

```
    ┌──────┐
    │  📦  │   68px circle
    │ name │   with Package icon
    │ [lib]│   and "lib" badge
    └──────┘
```

- Non-expandable, non-clickable for detail panel
- Supports focus states (focused, caller, callee, dim)
- Invisible handles for edge connections

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
        - X position = depth (dependency distance from entry)
        - Y position = stacked within file cluster
Step 6: Library nodes → simple horizontal row below the main graph
Step 7: Return { nodes, edges, clusters }
```

**Cluster coloring:** `getClusterColor(filepath, depth)` assigns deterministic HSL colors based on file path hash and depth level.

### `architectLayout(rawNodes, rawEdges)`

File-group view layout:

```
Step 1: Group all nodes by file
Step 2: Compute file-level BFS depth from entry file
Step 3: Create fileGroup composite nodes containing member lists
Step 4: Create inter-file edges (summarizing node-level edges)
Step 5: Layout file groups by depth
```

### `applyEdgeFocus(edges, focusId)`

Updates edge visual states for focus mode:
- **Active edges** (connected to focused node) → bright color, thicker stroke
- **Inactive edges** → dimmed, thinner
- **Cross-cluster edges** → dashed line style

### `langInfo(filename)`

Returns language metadata for file extension badges:

| Extension      | Label      | Color   |
|----------------|------------|---------|
| `.py`          | Python     | #3572A5 |
| `.js`          | JavaScript | #f1e05a |
| `.ts` / `.tsx` | TypeScript | #3178c6 |
| `.java`        | Java       | #b07219 |
| `.rs`          | Rust       | #dea584 |
| `.c` / `.cpp`  | C / C++    | #555555 |
| `.go`          | Go         | #00ADD8 |

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

| State    | Visual Treatment                              |
|----------|-----------------------------------------------|
| Default  | Glass-morphism card with subtle border        |
| Focused  | Accent border + glow shadow                   |
| Entry    | Green accent bar on left side                 |
| Caller   | Teal highlight                                |
| Callee   | Amber highlight                               |
| Dimmed   | Reduced opacity (0.3)                         |

### Animations

- `ez-spin` — Loading spinner rotation
- `ez-flow` — Edge flow animation (dashed line movement)
- `ez-edge-pulse-draw` — Edge drawing animation on focus
- `ez-node-expand-glow` — Node expansion glow effect
- `prefers-reduced-motion` — All animations disabled for accessibility

---

## State Management

The frontend uses **React local state** (no Redux/Zustand):

| State Category     | Storage             | Scope                         |
|--------------------|---------------------|-------------------------------|
| Graph data         | `useState` in CodeMap | Nodes, edges, clusters       |
| Focus state        | `useRef` + callback | Focus ID, applied via ref    |
| Tour/narration     | `TourContext`       | Global (shared via AppLayout)|
| Auth               | Clerk               | Global (ClerkProvider)       |
| WebSocket          | `useRef`            | Per-connection in CodeMap    |
| TTS state          | `useRef`            | Sentence queue, AudioContext |
| Chat history       | `useState`          | Per-session in CodeMap       |

### Why refs for focus?

The focus system uses `applyFocusRef` (a function stored in a ref) rather than state to avoid re-rendering the entire React Flow canvas when focus changes. This provides smooth, performant highlighting across hundreds of nodes.
