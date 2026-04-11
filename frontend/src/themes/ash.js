// Soft oat neutrals with ember-orange accents.
// Slots are the future-facing theme contract; palette keeps legacy semantic tokens alive.
export const slots = {
  c1Light: "#d8d0c4",
  c1Med: "#727983",
  c1Dark: "#343332",
  c2Light: "#f5c3a1",
  c2Med: "#c84a1b",
  c2Dark: "#91533b",
  veryLight: "#f7f1e8",
  veryDark: "#181514",
};

export const palette = {
  white: slots.veryLight,
  softWhite: "#ede4d8",
  mintGreen: slots.c2Light,
  pearGreen: slots.c2Med,
  black: slots.veryDark,
  purple: slots.c1Med,
  deepPurple: slots.c1Dark,
  indigo: "#272626",
  lavender: slots.c1Light,
  lavenderDark: "#9e9388",
  petrolBlue: slots.veryDark,
  violetGlow: "#efb28c",
};

const lightLayers = [
  `radial-gradient(1180px 620px at 16% 10%, color-mix(in oklab, ${slots.c1Light} 34%, transparent), transparent 62%)`,
  `radial-gradient(980px 540px at 86% 18%, color-mix(in oklab, ${slots.c2Light} 30%, transparent), transparent 66%)`,
  `radial-gradient(900px 520px at 46% 84%, color-mix(in oklab, ${slots.c2Med} 12%, transparent), transparent 72%)`,
  `linear-gradient(145deg, color-mix(in oklab, ${slots.veryLight} 88%, ${slots.c1Light} 12%), color-mix(in oklab, ${slots.veryLight} 84%, ${slots.c2Light} 16%))`,
];

const darkLayers = [
  `radial-gradient(1520px 800px at -8% 28%, color-mix(in oklab, ${slots.c1Dark} 62%, rgba(0, 0, 0, 0.6)), transparent 72%)`,
  `radial-gradient(1460px 760px at 110% 18%, color-mix(in oklab, ${slots.c2Med} 42%, rgba(78, 28, 16, 0.52)), transparent 74%)`,
  `radial-gradient(1180px 760px at 42% 118%, color-mix(in oklab, ${slots.c1Med} 36%, rgba(0, 0, 0, 0.46)), transparent 77%)`,
  `radial-gradient(920px 620px at -4% 116%, color-mix(in oklab, ${slots.c2Dark} 44%, rgba(0, 0, 0, 0.52)), transparent 84%)`,
  `radial-gradient(760px 460px at 12% -2%, rgba(247, 241, 232, 0.08), transparent 80%)`,
  `radial-gradient(620px 420px at 94% 98%, rgba(245, 195, 161, 0.12), transparent 86%)`,
  `linear-gradient(126deg, rgba(255, 255, 255, 0.07), rgba(255, 255, 255, 0) 42%, rgba(255, 255, 255, 0.03) 96%)`,
  `linear-gradient(210deg, rgba(24, 21, 20, 0.99), rgba(18, 16, 15, 0.97))`,
];

export const gradients = {
  light: lightLayers.join(", "),
  dark: darkLayers.join(", "),
};

export default { slots, palette, gradients };
