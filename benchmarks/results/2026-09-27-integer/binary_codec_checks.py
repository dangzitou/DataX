#!/usr/bin/env python3
"""Compile and run the shared binary codec/factory check; no database service required."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('runtime',type=Path)
    p.add_argument('output',type=Path)
    args=p.parse_args()
    runtime,output=args.runtime.resolve(),args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    plugins=['starrockswriter','doriswriter','selectdbwriter']
    for plugin in plugins:
        assert (runtime/'plugin/writer'/plugin).is_dir(), 'Build module '+plugin
    cp=os.pathsep.join([str(runtime/'lib/*')]+[str(runtime/'plugin/writer'/plugin/'*') for plugin in plugins])
    java=Path(os.environ['JAVA_HOME'])/'bin'
    source=Path(__file__).with_name('BinaryCodecCheck.java')
    subprocess.run([str(java/'javac'),'-cp',cp,'-d',str(output),str(source)],check=True)
    result=subprocess.run([str(java/'java'),'-cp',str(output)+os.pathsep+cp,'BinaryCodecCheck'],
                          text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (output/'result.log').write_text(result.stdout)
    (output/'results.json').write_text(json.dumps({'build':json.loads((runtime/'build-metadata.json').read_text()),
        'scope':__doc__,'exit_code':result.returncode,'output':result.stdout},indent=2))
    print(result.stdout,flush=True)
    result.check_returncode()


if __name__=='__main__':
    main()
