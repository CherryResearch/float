// Keep in sync with CSS variables defined in styles/theme.css.
// Slots are the future-facing theme contract; palette keeps legacy semantic tokens alive.
export const slots = {
  c1Light: "#f3f0ff",
  c1Med: "#4f46e5",
  c1Dark: "#3730a3",
  c2Light: "#86efac",
  c2Med: "#16a34a",
  c2Dark: "#166534",
  veryLight: "#ffffff",
  veryDark: "#0b1220",
};

export const palette = {
  white: slots.veryLight,
  softWhite: "#f6f8fb",
  mintGreen: "#22c55e",
  pearGreen: "#16a34a",
  black: slots.veryDark,
  purple: slots.c1Med,
  deepPurple: slots.c1Dark,
  indigo: "#390892", // used in dark mode styling elsewhere
  lavender: slots.c1Light,
  lavenderDark: "#e2e8f0",
  petrolBlue: "#051427",
  violetGlow: "#9f8df0",
};

const lightLayers = [
  `radial-gradient(1200px 600px at 20% 10%, color-mix(in oklab, ${slots.c1Light} 18%, transparent), transparent 60%)`,
  `radial-gradient(900px 500px at 85% 20%, color-mix(in oklab, ${slots.c2Light} 18%, transparent), transparent 60%)`,
  `radial-gradient(800px 400px at 40% 85%, color-mix(in oklab, ${slots.c1Light} 20%, transparent), transparent 70%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${slots.c1Light} 72%, ${slots.veryLight} 28%), color-mix(in oklab, ${palette.softWhite} 90%, #fff4f8 10%))`,
];

const darkLayers = [
  `radial-gradient(1500px 780px at -8% 32%, color-mix(in oklab, ${slots.c1Dark} 52%, rgba(0, 0, 0, 0.45)), transparent 72%)`,
  `radial-gradient(1500px 760px at 110% 22%, color-mix(in oklab, ${slots.c1Light} 34%, rgba(62, 15, 22, 0.56)), transparent 74%)`,
  `radial-gradient(1200px 840px at 40% 118%, color-mix(in oklab, ${slots.c2Med} 42%, rgba(0, 0, 0, 0.4)), transparent 75%)`,
  `radial-gradient(900px 580px at -6% 118%, color-mix(in oklab, ${slots.c2Dark} 44%, rgba(0, 0, 0, 0.5)), transparent 85%)`,
  `radial-gradient(820px 520px at 6% -3%, rgba(255, 255, 255, 0.08), transparent 80%)`,
  `radial-gradient(620px 420px at 96% 98%, rgba(255, 232, 239, 0.12), transparent 86%)`,
  `linear-gradient(128deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 48%, rgba(255, 255, 255, 0.05) 96%)`,
  `linear-gradient(210deg, rgba(2, 4, 10, 0.98), rgba(6, 7, 22, 0.92))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { slots, palette, gradients };
