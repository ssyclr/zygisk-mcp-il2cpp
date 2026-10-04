"""Exact ARM64 register values. Import/validation never contacts a target."""
import math

def schema():
    names = [*(f"{p}{i}" for p in ("x", "w") for i in range(31)),
             "sp", "pc", "fp", "lr", "pstate", "nzcv", "tpidr_el0", "tls", "fpsr", "fpcr",
             *(f"{p}{i}" for p in ("q", "v", "d", "s", "h", "b", "z") for i in range(32)),
             *(f"p{i}" for i in range(16)), "ffr"]
    return {"type":"object", "properties":{n:{"type":"string","minLength":1,"maxLength":16386} for n in names},
            "additionalProperties":False,"minProperties":1,"maxProperties":128,
            "description":"Exact bits; S/D accept float:1.5. W zero-extends X, SIMD low views preserve upper bits. Active SVE only, fixed VL; PSTATE permits NZCV only. Alias overlap rejected."}

def normalize(patch):
    result, used = {}, set()
    for name, value in patch.items():
        if not isinstance(value,str) or not value or len(value)>16386:
            raise ValueError("register values must be exact strings")
        scalar = name[:1] in ("s","d") and name[1:].isdigit()
        if scalar and value.startswith("float:"):
            number = float(value[6:])
            if not math.isfinite(number) or (name[0]=="s" and abs(number)>3.4028234663852886e38):
                raise ValueError("invalid or overflowing floating register")
            result[name]=value
        else:
            hexadecimal=value.startswith(("0x","0X"));digits=value[2:] if hexadecimal else value
            if not digits or any(c not in ("0123456789abcdefABCDEF" if hexadecimal else "0123456789") for c in digits):
                raise ValueError("invalid register bits")
            width=8
            if name[1:].isdigit():width={"w":4,"q":16,"v":16,"d":8,"s":4,"h":2,"b":1,"z":8192,"p":1024}.get(name[0],8)
            elif name in ("fpsr","fpcr"):width=4
            elif name=="ffr":width=1024
            n=int(digits,16 if hexadecimal else 10)
            if n.bit_length()>width*8 or (width>8 and not hexadecimal and n):
                raise ValueError("register width exceeded; vectors require hex bits")
            result[name]=hex(n)
        alias={"fp":"x29","lr":"x30","tls":"tpidr_el0","nzcv":"pstate"}.get(name,name)
        if name.startswith("w") and name[1:].isdigit():alias="x"+name[1:]
        if name[:1] in "qvdshbz" and name[1:].isdigit():alias="vector"+name[1:]
        if alias in used:raise ValueError("overlapping register aliases")
        used.add(alias)
    return result
