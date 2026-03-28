import React from "react";

export const Line = ({ width = "100%", height = 14, style = {} }) => (
  <div className="skeleton skeleton-line" style={{ width, height, ...style }} />
);

export const Rect = ({ width = "100%", height = 120, style = {} }) => (
  <div className="skeleton skeleton-rect" style={{ width, height, ...style }} />
);

export const Chips = ({ count = 3 }) => (
  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
    {Array.from({ length: count }).map((_, i) => (
      <div key={i} className="skeleton skeleton-chip" />
    ))}
  </div>
);

const PageSkeleton = () => (
  <div className="center-rail" style={{ paddingTop: 16 }}>
    <Line width="40%" />
    <Chips count={4} />
    <Rect height={180} />
    <Line width="60%" />
    <Line width="80%" />
    <Rect height={220} />
  </div>
);

export default PageSkeleton;

