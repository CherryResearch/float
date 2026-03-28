import React, { useEffect, useRef } from "react";
import * as d3 from "d3";

const data = [
  { label: "A", value: 30 },
  { label: "B", value: 80 },
  { label: "C", value: 45 },
  { label: "D", value: 60 },
  { label: "E", value: 20 },
];

const Visualization = () => {
  const ref = useRef(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    const width = 400;
    const height = 200;
    svg.attr("viewBox", [0, 0, width, height]);

    const x = d3
      .scaleBand()
      .domain(data.map((d) => d.label))
      .range([0, width])
      .padding(0.1);

    const y = d3
      .scaleLinear()
      .domain([0, d3.max(data, (d) => d.value)])
      .range([height, 0]);

    svg.selectAll("*").remove();

    svg
      .append("g")
      .attr("fill", "steelblue")
      .selectAll("rect")
      .data(data)
      .join("rect")
      .attr("x", (d) => x(d.label))
      .attr("y", (d) => y(d.value))
      .attr("height", (d) => y(0) - y(d.value))
      .attr("width", x.bandwidth());

    svg.append("g").call(d3.axisLeft(y));
    svg
      .append("g")
      .attr("transform", `translate(0,${height})`)
      .call(d3.axisBottom(x));
  }, []);

  return <svg ref={ref}></svg>;
};

export default Visualization;
