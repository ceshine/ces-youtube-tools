Write a watching guide for the YouTube video below, based on its transcript. The guide helps a reader decide what
to watch and jump straight to the parts they care about.

# Video

- Title: $title
- Channel: $channel
- URL: $url
- Length: $duration
- Uploaded: $upload_date

Chapters set by the uploader:

$chapters

Description:

$description

# Output format

Reply with the Markdown guide only: no preamble, no closing remarks, no code fence around it.

1. `# Watching Guide: <title>`. Use a short, plain title for what the video is about, not a copy of a clickbait
   title.
2. A line with the guide date and the channel: `$today · $channel`.
3. One paragraph: what kind of video it is (talk, interview, news show, tutorial, ...), its length, who speaks, and
   its main points. Name people only when the transcript or the description names them.
4. `**Watch the full video:** [youtube.com/watch?v=$video_id]($url). Every timestamp below jumps to that moment.`
   If the speakers are not named, add a sentence saying so.
5. `**How to use this guide:** ...` One or two sentences on the guide's structure, pointing to the segments that
   matter most.
6. Numbered segments that cover the whole video in order:
   - Heading: `## <number>. <short title>`.
   - The segment's range on its own line: `[<start>](<link>) to [<end>](<link>)`.
   - One or two sentences summarizing the segment.
   - Two to eight bullets, each `- [<time>](<link>): <highlight>`. A highlight is a claim, example, number,
     decision or turn in the conversation. Quote short, striking phrases exactly, in double quotes.
   - Bold a key term only when the segment defines or compares terms.
   - Use a table when the video compares several items along the same dimensions.

Choose segments by topic. Follow the uploader's chapters when there are any. Aim for one segment per two to four
minutes of a short video, and 8 to 15 segments for a long one.

# Timestamps

Every time in the guide is a link: `[<m:ss>](https://www.youtube.com/watch?v=$video_id&t=<seconds>s)`, or
`[<h:mm:ss>](...)` past one hour. Each transcript block below starts with its time in both forms, like
`[1:07 | 67s]`. Use those numbers exactly. Only link to times that start a transcript block, and pick the block
where the point is made.

# Accuracy

- Include only what the video says. Do not add outside facts, opinions or conclusions.
- $transcript_note
- Ignore sound tags like `[music]`. `>>` marks a change of speaker.
- Keep numbers, names and quotes as spoken. If something is unclear in the transcript, leave it out.

# Transcript

$transcript
