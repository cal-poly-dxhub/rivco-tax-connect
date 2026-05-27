export function generateStaticParams() { return [] }

import ChatSessionClient from "./client"

export default function ChatSessionPage({ params }: { params: Promise<{ id: string }> }) {
  return <ChatSessionClient params={params} />
}
