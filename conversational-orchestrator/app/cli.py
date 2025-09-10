from orchestrator.orchestrator import Orchestrator


def main():
    print("Conversational Orchestrator CLI — type 'exit' to quit\n")
    orc = Orchestrator()
    while True:
        x = input("you> ").strip()
        if x.lower() in {"exit", "quit"}:
            break
        resp = orc.handle(x)
        for m in resp.messages:
            print("agent>", m.content)
        if resp.artifacts:
            print("artifacts keys:", list(resp.artifacts.keys()))


if __name__ == "__main__":
    main()