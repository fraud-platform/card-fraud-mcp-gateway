# MCP Contract Outline

## Transport

- primary: remote Streamable HTTP
- secondary: local stdio wrapper for developer convenience only

## Contract Types

- tools
- resources
- prompts

## Contract Rules

- every tool must declare domain, read-only status, required scopes, and output truncation policy
- every resource must declare source of truth and sensitivity level
- every prompt must declare its intended user outcome and approved backing tools

## Initial Contract Families

- platform
- postgres
- redis
- kafka
- storage
- ops

## Launch Constraint

Phase 1 contracts must remain read-only.
