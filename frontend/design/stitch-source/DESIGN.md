---
name: AeroSilver Precision Instrumentation
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#434655'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#737686'
  outline-variant: '#c3c6d7'
  surface-tint: '#0053db'
  primary: '#004ac6'
  on-primary: '#ffffff'
  primary-container: '#2563eb'
  on-primary-container: '#eeefff'
  inverse-primary: '#b4c5ff'
  secondary: '#565e74'
  on-secondary: '#ffffff'
  secondary-container: '#dae2fd'
  on-secondary-container: '#5c647a'
  tertiary: '#006242'
  on-tertiary: '#ffffff'
  tertiary-container: '#007d55'
  on-tertiary-container: '#bdffdb'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dbe1ff'
  primary-fixed-dim: '#b4c5ff'
  on-primary-fixed: '#00174b'
  on-primary-fixed-variant: '#003ea8'
  secondary-fixed: '#dae2fd'
  secondary-fixed-dim: '#bec6e0'
  on-secondary-fixed: '#131b2e'
  on-secondary-fixed-variant: '#3f465c'
  tertiary-fixed: '#6ffbbe'
  tertiary-fixed-dim: '#4edea3'
  on-tertiary-fixed: '#002113'
  on-tertiary-fixed-variant: '#005236'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-xl:
    fontFamily: Space Grotesk
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-xl-mobile:
    fontFamily: Space Grotesk
    fontSize: 26px
    fontWeight: '600'
    lineHeight: 34px
    letterSpacing: -0.01em
  headline-lg:
    fontFamily: Space Grotesk
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 30px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Space Grotesk
    fontSize: 18px
    fontWeight: '500'
    lineHeight: 24px
  body-lg:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 22px
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  body-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 16px
  telemetry-display-xl:
    fontFamily: JetBrains Mono
    fontSize: 36px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.03em
  telemetry-display-lg:
    fontFamily: JetBrains Mono
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.02em
  telemetry-readout:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 18px
    letterSpacing: 0em
  label-caps:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '600'
    lineHeight: 14px
    letterSpacing: 0.08em
  label-sm:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  gutter: 0.75rem
  gutter-desktop: 1rem
  margin: 1rem
  margin-desktop: 1.5rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system delivers a tactile, scientific console aesthetic inspired by high-end laboratory instruments and aerospace telemetry stations. It reconciles technical rigor with an airy, polished physical presence: brushed anodized aluminum surfaces, matte-milled bezels, and crisp multi-axis data visualizations.

### Target Audience & Emotional Intent
- **Audience:** Aerospace diagnostic teams, biomechanics researchers, robotics operators, and industrial vibration engineers requiring continuous microsecond-level motion insight.
- **Emotional Response:** Surgical precision, calm authority, unshakeable stability, and technical sophistication without visual fatigue or cyber-kitsch noise.

### Style Archetype
- **Tactile Laboratory Minimalism:** Built upon milled metallic paneling, hairline divider tracks, micro-chamfered edges, and precise status LEDs. Tactility is expressed via subtle top-lit ambient bevels and distinct inset well structures rather than generic flat minimalism or exaggerated glassmorphism.

## Colors

The palette simulates matte anodized silver instrumentation plates punctuated by pure signal indicators and calibrated axis identifiers.

### Surface Architecture
- **Chassis Base (App Background):** `#EEF2F6` (Cool technical silver alloy)
- **Sub-chassis Trough (Recessed Viewports):** `#E2E8F0`
- **Module Panel (Raised Console Face):** `#FFFFFF`
- **Sub-panel / Card Header:** `#F8FAFC`
- **Structural Separation Borders:** `#DCE3EC` (Primary hairline edge) and `#CBD5E1` (Recessed channel border)

### Core Accent & Interaction
- **Primary (Instrument Cobalt):** `#2563EB` (Resting active), `#1D4ED8` (Pressed/Hover), `#60A5FA` (Soft focus glow)
- **Deep Steel Contrast (Headlines & Housing):** `#0F172A`

### 6-Axis Kinematic Vector Spectrum
These colors are reserved strictly for 6-DoF sensor metrics (accelerometer and gyroscope vectors) across waveform plots, vector spheres, and delta indicators:
- **X-Axis (Linear & Angular Pitch):** `#F97316` (Vivid Coral Ochre)
- **Y-Axis (Linear & Angular Roll):** `#10B981` (Telemetry Emerald)
- **Z-Axis (Linear & Angular Yaw):** `#0284C7` (Cobalt Cerulean)

### Telemetry State Semantics
- **Nominal / Telemetry Stream Active:** `#10B981` (Pulse LED tint `#ECFDF5`)
- **Telemetry Caution / Drift Alert:** `#F59E0B` (Warning ring tint `#FFFBEB`)
- **Impact / Fall Alarm / Critical Threshold:** `#EF4444` (Critical alert tint `#FEF2F2`)
- **Offline / Sensor Node Idle:** `#64748B` (Muted carrier tint `#F1F5F9`)

## Typography

The typography strategy reinforces physical instrument readouts:

1. **Space Grotesk (Display & Section Navigation):** Delivers clean mechanical geometry with humanized precision. Used strictly for workstation module headers, viewport coordinates, and panel labels.
2. **Inter (Body & Interface Mechanics):** Handles operational dense metadata, settings descriptions, modal dialogs, and tabular labels where pristine legibility at small sizes is vital.
3. **JetBrains Mono (Telemetry & Metrics):** Monospaced numeric rendering ensures zero layout jitter when live telemetry coordinates refresh at 60Hz-200Hz. Always render real-time values, sensor packet counts, timestamps, and axis readings with `font-variant-numeric: tabular-nums lining-nums`.

## Layout & Spacing

The workstation layout follows a dense, fluid modular rack model optimized for 1440px+ dual-monitor control centers down to ruggedized tablet screens.

### Grid Architecture
- **Control Center (Desktop ≥1280px):** 16-column fluid canvas with persistent 280px left telemetry tree, 1rem (16px) gutters, and 1.5rem (24px) chassis margins. Panels snap cleanly into 4, 8, or 12 column clusters.
- **Field Inspector (Tablet 768px - 1279px):** 8-column layout with collapsable diagnostics sidebar and 12px gutters. Waveform oscilloscopes drop secondary axis sub-plots into tabbed channels.
- **Mobile Diagnostic (Mobile <768px):** 4-column single-stack view with 8px internal padding. Critical alarm tiles and raw vector readouts lock to a high-priority sticky status band.

### Density Rules
- Structural container padding must maintain uniform `0.75rem` or `1rem` rhythm.
- Spacing inside telemetry clusters must be tight (`space-xs` and `space-sm`) to ensure axis pairs ($A_x, A_y, A_z$ / $G_x, G_y, G_z$) remain scannable as singular analytical units.

## Elevation & Depth

Visual hierarchy uses milled metallic depth rather than cast shadows, replicating an aluminum console with top-down studio illumination.

### Depth Hierarchy
1. **Recessed Well (Oscilloscope Viewports, Sensor Feeds):**
   - Background: `#F1F5F9`
   - Shadow: `inset 0 1px 2px rgba(15, 23, 42, 0.08), inset 0 0 0 1px #CBD5E1`
2. **Base Panel (Resting Diagnostic Card):**
   - Background: `#FFFFFF`
   - Border: `1px solid #DCE3EC`
   - Shadow: `0 1px 2px rgba(15, 23, 42, 0.04), 0 2px 6px rgba(15, 23, 42, 0.02)`
   - Highlight: Top edge pseudo-highlight `box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9)`
3. **Elevated Dial / Floating Tool Inspector:**
   - Background: `#FFFFFF`
   - Border: `1px solid #CBD5E1`
   - Shadow: `0 4px 12px rgba(15, 23, 42, 0.07), 0 1px 3px rgba(15, 23, 42, 0.04)`
4. **Alarm & Critical Modal State:**
   - Background: `#FFFFFF`
   - Border: `1px solid #FCA5A5`
   - Shadow: `0 8px 24px rgba(239, 68, 68, 0.12), 0 2px 6px rgba(15, 23, 42, 0.06)`

## Shapes

The interface balances sharp industrial edges with subtle ergonomic rounding:

- **Chassis Panels & Cards:** `0.5rem` (8px). Delivers a tight machined-aluminum contour without feeling bubbly or consumer-grade.
- **Recessed Viewports & Data Wells:** `0.375rem` (6px). Emphasizes mechanical containment.
- **Buttons, Axis Tags & Selectors:** `0.375rem` (6px).
- **Status LED Indicators & Micro Knobs:** Full circular pill (`9999px`).
- **Dividers & Metric Grooves:** Strict 1px rectilinear lines set to `#DCE3EC` or `#E2E8F0`.

## Components

### 1. Primary Action & Micro Buttons
- **Primary Execution Button:** Cobalt blue `#2563EB` fill, white `#FFFFFF` text, `0.375rem` corner radius, `1px solid #1D4ED8`. Top highlight: `inset 0 1px 0 rgba(255, 255, 255, 0.25)`. Active/Pressed: drops down 1px with `inset 0 2px 4px rgba(0, 0, 0, 0.2)`.
- **Instrument Secondary Button:** Metallic white `#FFFFFF` surface, border `1px solid #CBD5E1`, text `#0F172A`. Hover: `#F8FAFC` background with border `#94A3B8`.

### 2. Six-Axis Readout Badges & Vector Chips
- **Structural Spec:** JetBrains Mono 11px uppercase label, coupled with live tabular values.
- **X-Vector Tag:** Inset pill with `#FFF7ED` background, `#F97316` text, hairline border `1px solid #FFEDD5`.
- **Y-Vector Tag:** Inset pill with `#ECFDF5` background, `#10B981` text, hairline border `1px solid #D1FAE5`.
- **Z-Vector Tag:** Inset pill with `#F0F9FF` background, `#0284C7` text, hairline border `1px solid #E0F2FE`.

### 3. Telemetry Stream Cards & Modules
- Outer container: `#FFFFFF` surface, `0.5rem` radius, `1px solid #DCE3EC` border.
- Header strip: `#F8FAFC` background, height 36px, `0.5rem 0.5rem 0 0` radius, lower border `1px solid #DCE3EC`. Space Grotesk 12px bold uppercase title tracking at `0.05em`.
- Data viewport: Inset well with background `#F8FAFC`, surrounded by inner shadow and calibrated coordinate grid lines drawn with `#E2E8F0`.

### 4. Input Fields & Parameter Steppers
- Height 32px for maximum workspace density.
- Surface `#FFFFFF`, border `1px solid #CBD5E1`, font JetBrains Mono 13px.
- Focus state: Border `#2563EB`, outer glow `0 0 0 3px rgba(37, 99, 235, 0.15)`.

### 5. Checkboxes, Toggle Switches & Radio Selectors
- **Toggles:** Miniature physical slide switch (32px width, 18px height). Trough is recessed `#E2E8F0` when off, turning to `#2563EB` when online. Machined aluminum toggle knob in `#FFFFFF` with drop shadow `0 1px 2px rgba(15, 23, 42, 0.2)`.
- **Checkboxes:** 14px square, radius 3px, border `1.5px solid #94A3B8`. Checked state fills `#2563EB` displaying a sharp 1.5px white vector checkmark.

### 6. Domain-Specific Component: Telemetry Frequency Indicator (Heartbeat LED)
- 8px diameter beacon with an inner 4px bright core.
- Active sampling state flashes a soft ambient halo: `box-shadow: 0 0 8px #10B981`.
- Hardware fault triggers a warning pulse `0 0 8px #EF4444`.