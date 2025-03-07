#!/usr/bin/env python3

# This script runs tests using 'helm template'. It does not install anything to a cluster and runs fully locally.
# The yaml output generated by 'helm template' will be parsed and compared to see if expected/unexpected blocks
# are present.

# See the helm-tests/template-tests/sample.yaml file in this repo for a description of the expected structure
# of a test file.

import enum
import os
import sys
import yaml

# Initialize variables
filesCreated = []
retainTmpFiles = False
verbose = False

# Cleanup files created by this script
def cleanupTmpFiles():
    if not retainTmpFiles:
        printVerbose("Cleaning up tmp files...")
        for file in filesCreated:
            printVerbose("Removing file " + file)
            os.remove(file)

# Print the relevant error message and exit.
def exit(errorMessage):
    sys.stderr.write("❌ Error: %s\n" % errorMessage)
    sys.stderr.write("Run 'test_helm_template.py help' for more information\n")
    cleanupTmpFiles()
    sys.exit(1)

# Exit with a 0 return code
def exitSuccess():
    cleanupTmpFiles()
    sys.exit(0)

# Chosen operation
class Operation(enum.Enum):
    help = 1
    test = 2

# Parse command-line arguments
allowed_boolean_args = ["--retain-tmp-files", "--verbose"]
allowed_string_args = ["--test-file"]
def parseArgs():
    foundArgs = {}
    # First arg should be the chosen operation
    if len(sys.argv) > 1:
        op = sys.argv[1].lower()
    else:
        op = "help"
    try:
        operation = Operation[op.replace("-","_")]
        foundArgs['--operation'] = operation
    except KeyError:
        exit("Invalid operation: {}".format(op))

    # Read argument values
    checkingForValue = False
    currentArg = None
    for arg in sys.argv[2:]:
        if not checkingForValue:
            if arg.lower() in allowed_boolean_args:
                foundArgs[arg.lower()] = True
            elif arg.lower() in allowed_string_args:
                foundArgs[arg.lower()] = None
                checkingForValue = True
                currentArg = arg
            else:
                exit("Invalid arg: {}".format(arg))
        else:
            foundArgs[currentArg.lower()] = arg
            checkingForValue = False
            currentArg = None

    if checkingForValue:
        exit("No value provided for argument {}".format(currentArg))

    # Check for required args
    if foundArgs.get("--operation") == Operation.test and "--test-file" not in foundArgs:
        exit("A --test-file value must be provided when running a test")

    return foundArgs

# Print a description of the tool
def printHelp():
    print("""
Usage: test_helm_template.py OPERATION {options}
   where OPERATION in:
        help                        print this usage information

        test                        run a Helm template test
        
   where {options} include:
        --test-file                 Name of the file to be tested with 'helm template'.
                                    'helm template' will be run to generate a yaml file that
                                    will be checked for expected and unexpected yaml blocks.
                                    See files in the helm-tests/template-tests/ directory for
                                    examples of the expected file structre.

        --retain-tmp-files          When specified, the '/tmp/values.yaml' and '/tmp/template.yaml'
                                    files created during the test will not be deleted when the
                                    test finishes.

        --verbose                   When specified, verbose progress output will be written.

Examples:
    Run a Helm template test
    test_helm_template.py test --test-file helm-tests/template-tests/test-file.yaml
    """)

# Print message only if verbose output is enabled
def printVerbose(message):
    if verbose:
        print(message)

def printActualTemplate():
    print("helm template output:")
    with open('/tmp/template.yaml', 'r') as template:
        for line in template:
            print(line.rstrip())

class Section(enum.Enum):
    params = 1
    values = 2
    expected = 3
    unexpected = 4

# Parse the test file into separate sections
def parseTestFile(filename):
    sections = {
        Section.params: "",
        Section.values: "",
        Section.expected: "",
        Section.unexpected: ""
    }

    foundSections = set()

    # Parse the test file
    with open(filename, 'r') as testFile:
        currentSection = None
        for line in testFile:
            stripped = line.rstrip()
            if stripped == "### SECTION:PARAMETERS ###":
                currentSection = Section.params
                foundSections.add(currentSection)
            elif stripped == "### SECTION:VALUES ###":
                currentSection = Section.values
                foundSections.add(currentSection)
            elif stripped == "### SECTION:EXPECTED ###":
                currentSection = Section.expected
                foundSections.add(currentSection)
            elif stripped == "### SECTION:UNEXPECTED ###":
                currentSection = Section.unexpected
                foundSections.add(currentSection)
            elif currentSection:
                sections[currentSection] += line
    
    if Section.values not in foundSections:
        exit("No values section found in test file")

    if Section.expected not in foundSections and Section.unexpected not in foundSections:
        exit("No expected or unexpected sections found in test file")
    
    return sections

# Load yaml blocks from string, ignoring empty blocks
def loadAllNonEmptyBlocks(yamlString):
    blocks = []
    for block in yaml.safe_load_all(yamlString):
        if block:
            blocks.append(block)
    return blocks

# Ensure that a yaml block has the required fields
# needed to uniquely identify a k8s object in the template.
def validateBlock(block):
    if 'apiVersion' not in block or 'kind' not in block or 'metadata' not in block or 'name' not in block['metadata']:
        exit('YAML block is missing "apiVersion", "kind", and or "metadata.name" values. ' + 
             'These values must be provided for each block. Failed block: ' + str(block))

# Check if the apiVersion, kind, and metadata.name fields match in two yaml blocks
def versionKindNameMatch(obj1, obj2):
    return obj1['apiVersion'] == obj2['apiVersion'] and \
           obj1['kind'] == obj2['kind'] and \
           obj1['metadata']['name'] == obj2['metadata']['name']

# Check if expected values in a yaml block are included in another block
def expectedValuesFound(actual, expected):
    # If the elements are just simple strings, compare them here
    if type(actual) is str and type(expected) is str:
        return actual == expected

    simpleKeys = []
    for key in list(expected):
        if key not in actual or type(expected[key]) != type(actual[key]):
            return False

        # Dicts and lists need to be handled separately from the simple set comparison
        if type(expected[key]) is dict:
            if not expectedValuesFound(actual[key], expected[key]):
                return False
        elif type(expected[key]) is list:
            # Try to find a match for each list element
            actualElements = list(actual[key])
            for expectedEl in expected[key]:
                foundMatch = False
                toRemove = None
                for actualEl in actualElements:
                    if expectedValuesFound(actualEl, expectedEl):
                        foundMatch = True
                        toRemove = actualEl
                        break
                # Remove the matched element so the same one isn't used to match
                # multiple values from the expected elements
                if toRemove:
                    actualElements.remove(actualEl)
                if not foundMatch:
                    return False
        else:
            simpleKeys.append(key)
    # Verify that the remaining non-list, non-dict items are a subset of the actual object
    return { key: expected[key] for key in simpleKeys }.items() <= actual.items()

# Check if a match can be found for the expected yaml in the actual yaml.
# The actual yaml must contain an object that includes all elements in the expected block
# (the expected must be a subset of the match that is found)
def matchFound(actual, expected, warnForNoVersionKindNameMatch):
    for obj in actual:
        if versionKindNameMatch(obj, expected):
            return expectedValuesFound(obj, expected)
    if warnForNoVersionKindNameMatch:
        print(""" Warning: no version/kind/name match found for block.
                  Ensure correct version/kind/name is set in test file. Block: """)
        print(yaml.dump(expected))
    return False

# Main script processing
args = parseArgs()
operation = args.get("--operation")
testFile = args.get("--test-file")
retainTmpFiles = args.get("--retain-tmp-files")
if "--verbose" in args:
    verbose = True

if operation == Operation.help:
    printHelp()
    exitSuccess()

if operation == Operation.test:
    print("Running test from file: " + testFile + " ...")
    # Parse the test file
    printVerbose("Parsing test file " + testFile + " ...")
    sections = parseTestFile(testFile)
    params = next(yaml.safe_load_all(sections[Section.params]))
    expected = loadAllNonEmptyBlocks(sections[Section.expected])
    unexpected = loadAllNonEmptyBlocks(sections[Section.unexpected])

    if 'skipTest' in params and params['skipTest']:
        print("skipTest is set to true in PARAMETERS section. Skipping this test.")
        exitSuccess()

    # Validate the expected and unexpected blocks
    printVerbose("Validating expected blocks have required fields...")
    for block in expected:
        validateBlock(block)
    printVerbose("Validating unexpected blocks have required fields...")
    for block in unexpected:
        validateBlock(block)

    # Write the desired values to a file for 'helm template' to use
    printVerbose("Writing values to /tmp/values.yaml ...")
    with open('/tmp/values.yaml', 'w') as f:
        f.write(sections[Section.values])
        filesCreated.append('/tmp/values.yaml')

    # Use the filename as default release name
    releaseName = os.path.splitext(os.path.basename(testFile))[0]
    if 'releaseName' in params:
        releaseName = params['releaseName']

    # Run helm template based on the test params, write to a file
    helmCommand = "helm template " + releaseName + " charts/ping-devops -f /tmp/values.yaml > /tmp/template.yaml"
    printVerbose("Running helm template command: " + helmCommand + " ...")
    exitCode = os.system(helmCommand)
    if exitCode != 0:
        exit("Helm template command failed")
    else:
        filesCreated.append("/tmp/template.yaml")

    # Parse the template yaml file in
    template = []
    printVerbose("Reading generated template from /tmp/template.yaml ...")
    with open('/tmp/template.yaml', 'r') as f:
        objects = yaml.safe_load_all(f)
        for o in objects:
            validateBlock(o)
            template.append(o)

    printVerbose("Verifying that a match is found for each expected block...")
    for block in expected:
        if not matchFound(template, block, False):
            print("No match found for expected block:")
            print(yaml.dump(block))
            printActualTemplate()
            exit("Exiting as no match was found for an expected block")

    printVerbose("Verifying that a match is not found for any unexpected block...")
    for block in unexpected:
        if matchFound(template, block, True):
            print("Match found for unexpected block:")
            print(yaml.dump(block))
            printActualTemplate()
            exit("Exiting as a match was found for an unexpected block ")

    # If we made it this far, it's a pass!
    print("Test passed!")
    cleanupTmpFiles()
    printVerbose("Test script complete!")
