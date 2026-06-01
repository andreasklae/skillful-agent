"""Generate a personalised greeting."""
import argparse

STYLES = {
    "formal": "Good day, {name}. It is a pleasure to make your acquaintance.",
    "casual": "Hey {name}! Good to see you.",
    "enthusiastic": "WOW, {name}!!! SO great to meet you!! 🎉",
}

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--name", required=True, help="Name of the person to greet")
parser.add_argument(
    "--style",
    choices=["formal", "casual", "enthusiastic"],
    default="casual",
    help="Greeting style (default: casual)",
)
args = parser.parse_args()
print(STYLES[args.style].format(name=args.name))
