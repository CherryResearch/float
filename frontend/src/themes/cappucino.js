// Warm espresso + cherry palette. Keep the token names aligned with theme.css.
// Slots are the future-facing theme contract; palette keeps legacy semantic tokens alive.
export const slots = {
  c1Light: "#ebab77",
  c1Med: "#593217",
  c1Dark: "#130a01",
  c2Light: "#edc8d1",
  c2Med: "#bc1831",
  c2Dark: "#630712",
  veryLight: "#fffaf5",
  veryDark: "#0e0600",
};

export const palette = {
  white: slots.veryLight,
  softWhite: "#f8ecdf",
  mintGreen: "#e6c29f",
  pearGreen: "#b57a4a",
  black: slots.veryDark,
  purple: slots.c2Med,
  deepPurple: slots.c2Dark,
  indigo: slots.c1Dark,
  lavender: slots.c2Light,
  lavenderDark: "#f1c1cf",
  petrolBlue: slots.veryDark,
  violetGlow: "#ffc4d4",
};

const lightLayers = [
  `radial-gradient(1200px 620px at 18% 10%, color-mix(in oklab, ${slots.c2Light} 38%, transparent), transparent 62%)`,
  `radial-gradient(980px 540px at 86% 18%, color-mix(in oklab, ${slots.c1Light} 16%, transparent), transparent 66%)`,
  `radial-gradient(880px 540px at 50% 84%, color-mix(in oklab, ${slots.c1Light} 30%, transparent), transparent 74%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${slots.veryLight} 86%, ${slots.c1Light} 14%), color-mix(in oklab, ${slots.c2Light} 12%, ${slots.veryLight} 88%))`,
];

const darkLayers = [
  `radial-gradient(1520px 800px at -8% 30%, color-mix(in oklab, ${slots.c1Dark} 58%, rgba(0, 0, 0, 0.58)), transparent 72%)`,
  `radial-gradient(1460px 760px at 110% 20%, color-mix(in oklab, ${slots.c2Med} 50%, rgba(72, 10, 18, 0.52)), transparent 74%)`,
  `radial-gradient(1200px 780px at 42% 118%, color-mix(in oklab, ${slots.c1Med} 44%, rgba(0, 0, 0, 0.44)), transparent 77%)`,
  `radial-gradient(940px 620px at -4% 116%, color-mix(in oklab, ${slots.c2Dark} 46%, rgba(0, 0, 0, 0.5)), transparent 84%)`,
  `radial-gradient(860px 520px at 8% -4%, rgba(255, 247, 238, 0.08), transparent 80%)`,
  `radial-gradient(620px 420px at 96% 98%, rgba(255, 232, 239, 0.12), transparent 86%)`,
  `linear-gradient(126deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 48%, rgba(255, 255, 255, 0.04) 96%)`,
  `linear-gradient(210deg, rgba(19, 10, 1, 0.99), rgba(8, 4, 1, 0.97))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { slots, palette, gradients };
