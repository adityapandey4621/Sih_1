# Halcyon — approval workflow & compliance platform (frontend prototype)

Vite + React + TypeScript. A seven-scene scroll narrative with ambient, scroll-driven and interactive motion.
All objects are CSS/SVG/HTML 3D (no WebGL), so the page stays light and works without GPU access.

## Run
    npm install
    npm run dev      # development
    npm run build    # type-check + production build
    npm run preview

## Structure
    src/
      app/                 App shell + router
      components/          layout/, navigation/, ui/ (Button, SplitHeading, StatusPill, DemoControls, ScrollStage)
      features/home/
        sections/          Hero, Journey, Deadlines, Compliance, Audit, Authority, Closing
        components/        DossierVisual, WorkflowPipeline, SceneSection, SceneBackdrop, DepthLayer
        demo/              Configurable demo data + pure logic (workflow, deadlines, compliance, authority, audit hash chain)
        hooks/             useScrollDriven (follows scroll until the user takes over a demo)
        home.constants.ts  Copy for every scene
        home.css, scenes.css
      animation/           motion-config, variants, usePointerParallax
      lib/                 math, sha256 (sync, tested against node:crypto)
      styles/              tokens, global, animations

## Demo data
Institutions, IDs, dates, SLA values and escalation thresholds are fictional and live in `features/home/demo/*`.
Change them there; they are not statements about real AICTE rules.
