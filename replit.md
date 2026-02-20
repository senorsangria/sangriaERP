# sangriaERP

## Overview
Enterprise Resource Planning application built with Node.js and Express.

## Project Architecture
- **Backend**: Express.js (v5) server in `server.js`
- **Frontend**: Static HTML/CSS/JS served from `public/` directory
- **Port**: 5000 (frontend and API on same server)

## Structure
```
server.js          - Express server entry point
public/
  index.html       - Main HTML page
  styles.css       - Application styles
package.json       - Node.js dependencies
```

## Running
- `node server.js` starts the server on port 5000

## Recent Changes
- 2026-02-20: Initial project setup with Express 5, basic dashboard page
