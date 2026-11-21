// Headless: -postScript StringUsers.java <out.c> <string> [<string> ...]
// Finds each string (NUL-terminated) in memory, lists the references to it and decompiles the functions
// that use it. Read-only.
import java.io.PrintWriter;
import java.util.HashSet;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class StringUsers extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        DecompInterface ifc = new DecompInterface();
        ifc.openProgram(currentProgram);
        HashSet<Function> done = new HashSet<>();
        try (PrintWriter w = new PrintWriter(args[0], "UTF-8")) {
            for (int i = 1; i < args.length; i++) {
                byte[] pat = (args[i].replace("#", "%") + "\0").getBytes("ISO-8859-1");  // # stands for % (batch)
                Address start = currentProgram.getMinAddress();
                Address hit;
                int found = 0;
                while ((hit = find(start, pat)) != null && found < 8) {
                    found++;
                    w.printf("== \"%s\" at %s%n", args[i], hit);
                    for (Reference r : getReferencesTo(hit)) {
                        Function f = getFunctionContaining(r.getFromAddress());
                        w.printf("   ref %s in %s%n", r.getFromAddress(), f == null ? "?" : f.getName() + "@" + f.getEntryPoint());
                        if (f != null && done.add(f)) {
                            DecompileResults res = ifc.decompileFunction(f, 120, monitor);
                            w.printf("// ---- %s @ %s ----%n", f.getName(), f.getEntryPoint());
                            w.println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// failed");
                        }
                    }
                    start = hit.add(1);
                }
                if (found == 0) w.printf("== \"%s\" not found%n", args[i]);
            }
        }
        ifc.dispose();
    }
}
