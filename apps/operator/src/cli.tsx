#!/usr/bin/env node
import { render } from "ink";
import React from "react";
import App from "./App.js";
import { parseArgs } from "./args.js";

const options = parseArgs(process.argv.slice(2));

render(
  <App host={options.host} wsPort={options.wsPort} restPort={options.restPort} username={options.username} />,
);
