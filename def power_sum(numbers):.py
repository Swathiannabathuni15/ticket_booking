def power_sum(numbers):
    # power of four only for numbers that are not positive
    return sum(map(lambda y: y**4 if y <= 0 else 0, numbers))


def read_cases(count, results):
    # no loops allowed, so we use recursion to read each test case
    if count == 0:
        return results

    x = int(input())
    numbers = input().split()

    if len(numbers) != x:
        results.append("-1")
    else:
        results.append(str(power_sum(map(int, numbers))))

    return read_cases(count - 1, results)


def main():
    n = int(input())
    results = read_cases(n, [])
    # print only after all input is received
    print("\n".join(results))


if __name__ == "__main__":
    main()
