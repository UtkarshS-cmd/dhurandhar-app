@echo off
rem Compatibility wrapper — the canonical cross-platform launcher is
rem e2e-server.cjs (used directly by playwright.config.cjs).
node "%~dp0e2e-server.cjs" %*
