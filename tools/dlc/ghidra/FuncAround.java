// Headless: -postScript FuncAround.java <out.c> <addr> [<addr> ...]
// PowerPC: for code outside any function, walk back to the nearest prologue (mflr r12 = 7D8802A6, or
// stwu r1,-x(r1) = 9421xxxx after a blr), create the function there and decompile it.
import java.io.PrintWriter;
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.cmd.function.CreateFunctionCmd;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class FuncAround extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        try (PrintWriter w = new PrintWriter(args[0], "UTF-8")) {
            for (int i = 1; i < args.length; i++) {
                Address a = toAddr(args[i]);
                Function f = getFunctionContaining(a);
                if (f == null) {
                    Address p = a;
                    Address start = null;
                    for (int k = 0; k < 0x4000 && start == null; k += 4) {
                        p = a.subtract(k);
                        int v = getInt(p);
                        if (v == 0x7D8802A6) start = p;  // mflr r12
                        else if (k > 0 && getInt(p.subtract(4)) == 0x4E800020 && (v >>> 16) == 0x9421) start = p;  // blr; stwu r1
                    }
                    if (start != null) {
                        new DisassembleCommand(start, null, true).applyTo(currentProgram, monitor);
                        new CreateFunctionCmd(start).applyTo(currentProgram, monitor);
                        f = getFunctionAt(start);
                    }
                }
                if (f == null) { w.printf("// %s: no function%n%n", args[i]); continue; }
                DecompileResults r = ifc.decompileFunction(f, 180, monitor);
                w.printf("// ---- %s @ %s (contains %s) ----%n", f.getName(true), f.getEntryPoint(), args[i]);
                w.println(r.decompileCompleted() ? r.getDecompiledFunction().getC() : "// failed: " + r.getErrorMessage());
            }
        }
        ifc.dispose();
    }
}
