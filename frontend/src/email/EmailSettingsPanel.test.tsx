import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/setup";
import EmailSettingsPanel from "./EmailSettingsPanel";
import type { EmailAccount } from "./api";

const account: EmailAccount = {
  address: "me@gmail.com",
  username: "me@gmail.com",
  imap_host: "imap.gmail.com",
  imap_port: 993,
  imap_security: "ssl",
  smtp_host: "smtp.gmail.com",
  smtp_port: 465,
  smtp_security: "ssl",
  has_password: true,
};

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <EmailSettingsPanel open onOpenChange={() => {}} />
    </QueryClientProvider>,
  );
}

describe("EmailSettingsPanel", () => {
  it("shows disabled notice on 503", async () => {
    server.use(
      http.get("*/api/email/account", () =>
        HttpResponse.json(
          { detail: "Email is not configured on this server." },
          { status: 503 },
        ),
      ),
    );
    renderPanel();
    expect(
      await screen.findByText("Email isn't enabled on this server."),
    ).toBeVisible();
    expect(screen.queryByLabelText("Email address")).not.toBeInTheDocument();
  });

  it("gmail preset fills hosts and ports", async () => {
    server.use(
      http.get(
        "*/api/email/account",
        () => new HttpResponse(null, { status: 404 }),
      ),
    );
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Gmail" }));
    expect(screen.getByLabelText("IMAP host")).toHaveValue("imap.gmail.com");
    expect(screen.getByLabelText("IMAP port")).toHaveValue(993);
    expect(screen.getByLabelText("SMTP host")).toHaveValue("smtp.gmail.com");
    expect(screen.getByLabelText("SMTP port")).toHaveValue(465);
  });

  it("save sends form without password when left blank for existing account", async () => {
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.get("*/api/email/account", () => HttpResponse.json(account)),
      http.put("*/api/email/account", async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(account);
      }),
    );
    renderPanel();
    const password = await screen.findByLabelText("App password");
    expect(password).toHaveAttribute("placeholder", "•••••• saved");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent).not.toBeNull());
    expect(sent).toMatchObject({
      address: "me@gmail.com",
      imap_host: "imap.gmail.com",
    });
    expect(sent).not.toHaveProperty("password");
  });

  it("shows server error text from failed test connection", async () => {
    server.use(
      http.get(
        "*/api/email/account",
        () => new HttpResponse(null, { status: 404 }),
      ),
      http.post("*/api/email/account/test", () =>
        HttpResponse.json({
          ok: false,
          error: "Login failed: bad credentials",
        }),
      ),
    );
    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: "Gmail" }));
    await userEvent.type(
      screen.getByLabelText("Email address"),
      "me@gmail.com",
    );
    await userEvent.type(screen.getByLabelText("App password"), "secret");
    await userEvent.click(
      screen.getByRole("button", { name: "Test connection" }),
    );
    expect(
      await screen.findByText("Login failed: bad credentials"),
    ).toBeVisible();
  });

  it("disconnect asks for confirmation then deletes", async () => {
    let deleted = false;
    server.use(
      http.get("*/api/email/account", () =>
        deleted
          ? new HttpResponse(null, { status: 404 })
          : HttpResponse.json(account),
      ),
      http.delete("*/api/email/account", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderPanel();
    await userEvent.click(
      await screen.findByRole("button", { name: "Disconnect" }),
    );
    expect(deleted).toBe(false);
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Disconnect" }),
    );
    await waitFor(() => expect(deleted).toBe(true));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Disconnect" }),
      ).not.toBeInTheDocument(),
    );
  });
});
