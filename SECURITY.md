# Security Policy

## Important: POKE Controls Hardware

POKE injects machine code directly into bare-metal devices. A malicious or incorrect binary can:
- Brick a device by writing to protected registers
- Cause physical damage to connected hardware
- Exfiltrate data from device memory

**Never run a POKE hub on untrusted networks without authentication.**

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x     | Yes       |

## Reporting a Vulnerability

If you discover a security vulnerability, please:

1. **Do NOT open a public issue**
2. Email: qct8377@gmail.com or use GitHub's private vulnerability reporting
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
4. We will respond within 48 hours

## Security Considerations

### Hub Security
- The hub has full control over all connected edges
- Hub compromise = all edges compromised
- Always run the hub behind authentication
- Use HTTPS for hub communication

### Edge Security
- Edges execute arbitrary machine code — by design
- Physical access to an edge = full control
- Edges should not be exposed to the public internet directly

### Device Profiles
- Profiles from untrusted sources could contain harmful register writes
- Always review profiles before using them
- Official/verified profiles will be marked in the marketplace (future)

### LLM Safety
- LLM-generated code could contain errors that damage hardware
- Critical operations should be validated against device profiles
- Consider simulation before execution on valuable hardware
